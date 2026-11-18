from .management.commands.export_daily_activities import Command

def export_daily_activities():
    Command().handle()
