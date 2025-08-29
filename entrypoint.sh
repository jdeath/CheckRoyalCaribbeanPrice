#!/bin/sh

# Check if the first argument is "check"
if [ "$1" = "check" ]; then
    # Execute the Python script directly for single price check
    exec python CheckRoyalCaribbeanPrice.py
fi

# If other arguments are provided, execute them
if [ $# -gt 0 ]; then
    exec "$@"
fi

# Otherwise, set up cron for scheduled execution (default behavior)
# Set default cron schedule if not provided
if [ -z "$CRON_SCHEDULE" ]; then
    CRON_SCHEDULE="0 7,19 * * *"
fi

# Create crontab with the specified schedule
echo "$CRON_SCHEDULE cd /app && python CheckRoyalCaribbeanPrice.py >> /proc/1/fd/1 2>&1" > /etc/crontabs/root

# Set permissions for crontab
chmod 0600 /etc/crontabs/root

# Start crond in foreground
echo "Starting crond with schedule: $CRON_SCHEDULE"
exec crond -f -d 8