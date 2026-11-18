"""
posApp/receipt_printing.py

Sends a finished sale straight to the Windows printer (no browser, no dialog).
Works when Django runs on the same PC the printer is connected to.

Needs:  pip install pywin32
"""
import base64
import importlib
import io
import json
import logging
import re
import subprocess
import textwrap
import time
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)

# characters per line for each paper size
PAPER_COLUMNS = {"58": 32, "80": 42, "A4": 80}

ESC_INIT = b"\x1b@"
BOLD_ON = b"\x1bE\x01"
BOLD_OFF = b"\x1bE\x00"
CUT = b"\x1dV\x01"          # partial cut (ignored by printers without a cutter)


def _clean(value):
    """Settings that were saved as the word 'None' (or empty) count as blank."""
    text = "" if value is None else str(value).strip()
    return "" if text.lower() == "none" else text


def _dec(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _money(value):
    return f"{value:,.2f}"


def _row(left, right, width):
    """left text, right text, padded to the line width."""
    left, right = str(left), str(right)
    space = width - len(left) - len(right)
    if space < 1:
        left = left[: max(0, width - len(right) - 1)]
        space = 1
    return left + " " * space + right


PAPER_DOTS = {"58": 384, "80": 576}      # printable dots per line on thermal paper
GS_RASTER = b"\x1dv0\x00"                # ESC/POS "print raster bit image"


def _safe(make):
    """Run an image step; if it fails the receipt still prints without it."""
    try:
        return make() or b""
    except Exception:
        logger.exception("Receipt image skipped")
        return b""


def _raster_commands(canvas, dither):
    """Turn a PIL image (width = full paper width) into ESC/POS raster commands."""
    from PIL import Image, ImageOps

    gray = canvas.convert("L")
    if not dither:
        gray = gray.point(lambda v: 255 if v >= 128 else 0)
    method = getattr(Image, "Dither", Image)
    ink = ImageOps.invert(gray).convert(
        "1", dither=method.FLOYDSTEINBERG if dither else method.NONE
    )

    width, height = ink.size
    row_bytes = width // 8
    data = ink.tobytes()

    out = []
    band = 64                                   # send in strips so small printers never choke
    for y in range(0, height, band):
        rows = min(band, height - y)
        out.append(GS_RASTER + bytes([row_bytes % 256, row_bytes // 256, rows % 256, rows // 256]))
        out.append(data[y * row_bytes:(y + rows) * row_bytes])
    out.append(b"\n")
    return b"".join(out)


def _logo_raster(logo_name, dot_width):
    """Print the logo file that was in use when the sale was made."""
    if not logo_name:
        return b""
    from django.core.files.storage import default_storage
    from PIL import Image

    if not default_storage.exists(logo_name):
        return b""
    with default_storage.open(logo_name, "rb") as handle:
        raw = handle.read()

    src = Image.open(io.BytesIO(raw)).convert("RGBA")
    white = Image.new("RGBA", src.size, (255, 255, 255, 255))
    white.alpha_composite(src)                  # transparent PNG background becomes white
    img = white.convert("L")
    img.thumbnail((int(dot_width * 0.6), 110), Image.LANCZOS)

    canvas = Image.new("L", (dot_width, img.height), 255)
    canvas.paste(img, ((dot_width - img.width) // 2, 0))      # centred
    return _raster_commands(canvas, dither=True)


def _initials_raster(text, dot_width):
    """The round shop/branch initials badge shown on screen when no logo was set."""
    from PIL import Image, ImageDraw, ImageFont

    text = (text or "SB")[:3].upper()
    scale = dot_width / 384
    w, h = int(130 * scale), int(76 * scale)
    size = int(h * 0.62)

    font = None
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()

    canvas = Image.new("L", (dot_width, h + 10), 255)
    draw = ImageDraw.Draw(canvas)
    left = (dot_width - w) // 2
    draw.ellipse((left, 4, left + w, 4 + h), outline=0, width=max(2, int(3 * scale)))
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text((left + (w - tw) // 2 - box[0], 4 + (h - th) // 2 - box[1]), text, fill=0, font=font)
    return _raster_commands(canvas, dither=False)


def _load_barcode_png(code):
    """Reuse the project's own generate_barcode() so print matches the screen receipt."""
    package = __package__ or "posApp"
    for module_name in ("views", "utils"):
        try:
            module = importlib.import_module(f"{package}.{module_name}")
            generate = getattr(module, "generate_barcode")
        except Exception:
            continue
        result = generate(code)
        if not result:
            return None
        if isinstance(result, str):
            if result.startswith("data:"):
                result = result.split(",", 1)[1]
            result = result.encode()
        try:
            return base64.b64decode(result, validate=False)
        except Exception:
            return bytes(result)
    return None


def _barcode_raster(code, dot_width):
    png = _load_barcode_png(code)
    if not png:
        return b""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(png)).convert("L")
    box = ImageOps.invert(img).getbbox()        # trim the empty margin around the barcode
    if box:
        img = img.crop(box)

    # keep only the bars; the digits are printed below as normal text (the picture's own
    # digits can be clipped at the edges)
    pixels = img.load()
    width, height = img.size
    seen_ink, end = False, height
    for y in range(height):
        row_has_ink = any(pixels[x, y] < 128 for x in range(width))
        if row_has_ink:
            seen_ink = True
        elif seen_ink:
            end = y
            break
    img = img.crop((0, 0, width, end))
    box = ImageOps.invert(img).getbbox()
    if box:
        img = img.crop(box)

    scale = min(int(dot_width * 0.85) / img.width, 150 / img.height)
    if scale >= 1:
        factor = int(scale)                     # whole-number zoom keeps the bars sharp
        img = img.resize((img.width * factor, img.height * factor), Image.NEAREST)
    else:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.LANCZOS)

    canvas = Image.new("L", (dot_width, img.height + 8), 255)
    canvas.paste(img, ((dot_width - img.width) // 2, 4))
    return _raster_commands(canvas, dither=False)


def build_receipt_bytes(transaction, items, user, printer_settings, fmt, reprint=False):
    paper = str(getattr(printer_settings, "paper_size", "80") or "80")
    thermal = paper in ("58", "80")
    width = PAPER_COLUMNS.get(paper, 42)

    rate = _dec(transaction.exchange_rate) or Decimal("1")
    currency = transaction.currency or ""

    parts = []

    def text(line="", bold=False):
        if bold and thermal:
            parts.append(BOLD_ON)
        parts.append((line + "\n").encode("cp437", errors="replace"))
        if bold and thermal:
            parts.append(BOLD_OFF)

    def center(line, bold=False):
        text(line.center(width).rstrip(), bold)

    def rule():
        text("-" * width)

    if thermal:
        parts.append(ESC_INIT)
        top_image = _safe(lambda: _logo_raster(fmt.get("logo"), PAPER_DOTS[paper]))
        if not top_image:            # no logo at the time of the sale: print the initials badge
            top_image = _safe(lambda: _initials_raster(fmt.get("initials"), PAPER_DOTS[paper]))
        parts.append(top_image)

    # ---- header -------------------------------------------------------
    header = _clean(fmt.get("header_name")) or f"{transaction.shop.name} - {transaction.branch.name}"
    center(header.upper(), bold=True)
    extra_header = _clean(fmt.get("header_text"))
    for line in extra_header.splitlines():
        center(line.strip())
    if reprint:
        center("*** REPRINT ***", bold=True)
    rule()

    cashier = (f"{user.first_name} {user.last_name}").strip() or user.get_username()
    text(f"Cashier: {cashier.title()}")
    if transaction.customer_name:
        text(f"Customer: {str(transaction.customer_name).title()}")
    when = transaction.date_added
    if timezone.is_aware(when):
        when = timezone.localtime(when)
    text(f"Date: {when.strftime('%d %b %Y %H:%M')}")
    text(f"Code: {transaction.code}")
    text(f"Receipt No: {transaction.receipt_number or 'N/A'}")
    text(f"Currency: {currency}")
    rule()

    # ---- items + tax (same rules as the receipt view) -----------------
    inclusive_total = exclusive_total = Decimal("0")
    inclusive_tax_total = exclusive_tax_total = Decimal("0")

    text(_row("Item", "Total", width), bold=True)
    for item in items:
        price = _dec(item.price)
        line_total = price * item.qty
        inc_rate = _dec(item.product.inclusive_tax)
        exc_rate = _dec(item.product.exclusive_tax)

        if inc_rate > 0:
            inclusive_tax_total += line_total * inc_rate / 100
            inclusive_total += line_total
        elif exc_rate > 0:
            exclusive_tax_total += line_total * exc_rate / 100
            exclusive_total += line_total
        else:
            inclusive_total += line_total

        for name_line in textwrap.wrap(str(item.product.name), width) or [""]:
            text(name_line)
        text(_row(f"  {item.qty} x {_money(price * rate)}", _money(line_total * rate), width))

    rule()

    # Price total is the final amount. Exclusive tax is carved out of it:
    # Sub Total = price total - exclusive tax, Grand Total = price total
    price_total = inclusive_total + exclusive_total
    sub_total = (price_total - exclusive_tax_total) * rate
    grand_total = price_total * rate

    # ---- totals -------------------------------------------------------
    text(_row("Subtotal:", f"{currency} {_money(sub_total)}", width))
    if inclusive_tax_total > 0:
        text(_row("Inclusive Tax:", f"{currency} {_money(inclusive_tax_total * rate)}", width))
    if exclusive_tax_total > 0:
        text(_row("Exclusive Tax:", f"{currency} {_money(exclusive_tax_total * rate)}", width))
    text(_row("GRAND TOTAL:", f"{currency} {_money(grand_total)}", width), bold=True)
    text(_row("Tendered:", f"{currency} {_money(_dec(transaction.tendered_amount))}", width))
    text(_row("Change:", f"{currency} {_money(_dec(transaction.amount_change))}", width))
    rule()

    if thermal:
        barcode_data = _safe(lambda: _barcode_raster(transaction.code, PAPER_DOTS[paper]))
        parts.append(barcode_data)
        if barcode_data:
            center(str(transaction.code))

    # ---- footer -------------------------------------------------------
    footer = _clean(fmt.get("footer_text")) or "Thank you for shopping with us!"
    for line in footer.splitlines():
        center(line.strip())
    if fmt.get("show_timestamp"):
        center(timezone.localtime().strftime("%d %b %Y %H:%M"))

    if thermal:
        parts.append(b"\n\n\n\n")
        parts.append(CUT)
    else:
        parts.append(b"\f")          # eject the page on A4 printers

    return b"".join(parts)


# ----------------------------------------------------------------------
# Receipt format snapshot: every receipt keeps the format it was created with,
# so changing the logo / header / footer later never alters old receipts.
# ----------------------------------------------------------------------
DEFAULT_FOOTER = "Thank you for shopping with us!"


def _build_format(transaction):
    from .models import PrinterSettings, ReceiptSettings

    rs, _ = ReceiptSettings.objects.get_or_create(branch=transaction.branch)
    ps = PrinterSettings.objects.filter(shop=transaction.shop).first()

    shop_name = transaction.shop.name
    branch_name = transaction.branch.name
    logo = getattr(rs, "logo", None)

    return {
        "header_name": _clean(getattr(rs, "custom_header_name", "")) or f"{shop_name} - {branch_name}",
        "initials": ((shop_name or "S")[:1] + (branch_name or "B")[:1]).upper(),
        "logo": logo.name if logo else "",
        "footer_text": _clean(getattr(rs, "footer_text", ""))
                       or _clean(getattr(ps, "footer_text", "")) or DEFAULT_FOOTER,
        "header_text": _clean(getattr(ps, "header_text", "")),
        "show_timestamp": bool(getattr(rs, "show_timestamp", False)),
        "paper_width": getattr(rs, "paper_width", None),
        "margin_top": getattr(rs, "margin_top", None),
        "margin_bottom": getattr(rs, "margin_bottom", None),
    }


def ensure_receipt_format(transaction):
    """Freeze the current format onto the sale (only the first time)."""
    existing = getattr(transaction, "receipt_format", None)
    if existing:
        try:
            return json.loads(existing)
        except ValueError:
            pass
    fmt = _build_format(transaction)
    transaction.receipt_format = json.dumps(fmt)
    transaction.save(update_fields=["receipt_format"])
    return fmt


def get_receipt_format(transaction):
    """The format stored with this sale. Older sales are frozen the first time they are used."""
    return ensure_receipt_format(transaction)


def _printer_name(printer_settings):
    """Printer chosen on the Printer Settings page, else the Windows default."""
    for field in ("printer_name", "printer", "default_printer"):
        value = getattr(printer_settings, field, None)
        if value:
            return str(value)
    return None


SERIAL_BAUD = 9600          # Bluetooth serial ports ignore this, but it must be set


_PORT_CACHE = {"time": 0, "data": None}


def _bluetooth_names():
    """{MAC: device name} for paired Bluetooth devices, read through Windows PowerShell."""
    cmd = ["powershell", "-NoProfile", "-Command",
           "Get-PnpDevice -Class Bluetooth | Select-Object FriendlyName,InstanceId | ConvertTo-Json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        data = json.loads(out) if out.strip() else []
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    names = {}
    for item in data:
        match = re.search(r"DEV_([0-9A-Fa-f]{12})", item.get("InstanceId") or "")
        if match and item.get("FriendlyName"):
            names[match.group(1).upper()] = item["FriendlyName"]
    return names


def serial_printers(refresh=False):
    """[(real name, COM port)] for paired Bluetooth printers and USB serial devices."""
    now = time.time()
    if not refresh and _PORT_CACHE["data"] is not None and now - _PORT_CACHE["time"] < 300:
        return _PORT_CACHE["data"]
    try:
        from serial.tools import list_ports          # pip install pyserial
    except ImportError:
        return []

    bluetooth = _bluetooth_names()
    outgoing, incoming = [], []
    for port in list_ports.comports():
        hwid = (port.hwid or "").upper()
        mac = None
        if "BTHENUM" in hwid:
            match = re.search(r"&([0-9A-F]{12})_", hwid)
            mac = match.group(1) if match else None
        if mac and mac in bluetooth:
            name = bluetooth[mac]
        else:
            name = re.sub(r"\s*\(COM\d+\)\s*$", "", port.description or "") or port.device
        # Bluetooth "incoming" ports have an all-zero address; the outgoing one is the printer
        (incoming if mac == "0" * 12 else outgoing).append((name, port.device))

    found, seen = [], set()
    for name, device in (outgoing or incoming):
        if name not in seen:
            seen.add(name)
            found.append((name, device))
    _PORT_CACHE.update(time=now, data=found)
    return found


def _windows_printer_names():
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]
    except ImportError:
        return []


def list_printers():
    """Real printer names for the Printer Settings dropdown."""
    names = _windows_printer_names() + [name for name, _ in serial_printers(refresh=True)]
    return sorted(set(names))


def _send_serial(port, data):
    """Bluetooth (SPP) printers show up in Windows as a COM port such as COM5."""
    import time
    import serial                                    # pip install pyserial

    try:
        conn = serial.Serial(port, SERIAL_BAUD, timeout=5, write_timeout=10)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open {port}. Is the printer switched on and paired with this PC? ({exc})"
        )
    try:
        for i in range(0, len(data), 512):           # small chunks so the printer buffer never overflows
            conn.write(data[i:i + 512])
            conn.flush()
            time.sleep(0.05)
        time.sleep(1)                                # let the printer finish before the port closes
    finally:
        conn.close()


def _find_port(name, refresh=False):
    for printer, device in serial_printers(refresh):
        if printer == name:
            return device
    return None


def send_raw_to_printer(printer_name, data):
    name = (printer_name or "").strip()

    if name:
        # old settings may still hold a plain port such as "COM5"
        if re.fullmatch(r"COM\d+", name.upper()):
            return _send_serial(name.upper(), data)

        port = _find_port(name)
        if port is None and name not in _windows_printer_names():
            port = _find_port(name, refresh=True)      # printer may have been paired just now
        if port:
            return _send_serial(port, data)

    import win32print         

    name = name or win32print.GetDefaultPrinter()
    try:
        handle = win32print.OpenPrinter(name)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot find printer '{name}'. Check that it is switched on, paired with this PC, "
            f"and selected in Printer Settings. ({exc})"
        )
    try:
        win32print.StartDocPrinter(handle, 1, ("POS Receipt", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def print_transaction_receipt(transaction, user, reprint=False, force=False):
   
    from .models import SalesItem, PrinterSettings

    printer_settings = PrinterSettings.objects.filter(shop=transaction.shop).first()
    if printer_settings is not None:
        if not (reprint or force) and not getattr(printer_settings, "auto_print", True):
            return False
        
        if str(getattr(printer_settings, "connection_type", "system")).lower() == "ble":
            return False

    fmt = get_receipt_format(transaction)      
    items = list(SalesItem.objects.filter(transaction=transaction).select_related("product"))

    data = build_receipt_bytes(transaction, items, user, printer_settings, fmt, reprint=reprint)
    send_raw_to_printer(_printer_name(printer_settings), data)
    return True


def print_test_page(printer_settings):
    """Small test print for the Printer Settings page."""
    paper = str(getattr(printer_settings, "paper_size", "80") or "80")
    thermal = paper in ("58", "80")
    width = PAPER_COLUMNS.get(paper, 42)
    lines = [
        "PRINTER WORKING WELL".center(width),
        "-" * width,
        f"Paper size: {paper}".ljust(width),
        "TEST PAGE" * (width // 10) + "1234567890"[: width % 10],
        "-" * width,
    ]
    data = (ESC_INIT if thermal else b"") + ("\n".join(lines) + "\n").encode("cp437", errors="replace")
    data += (b"\n\n\n\n" + CUT) if thermal else b"\f"
    send_raw_to_printer(_printer_name(printer_settings), data)
