from app.services.notifier import notify_upcoming

if __name__ == "__main__":
    notify_upcoming(days_before=3)
