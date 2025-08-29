FROM python:3.12-alpine

# Install timezone data
RUN apk add --no-cache tzdata

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY CheckRoyalCaribbeanPrice.py .
COPY entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create directory for cron logs
RUN mkdir -p /var/log

# Set default environment variables
ENV CRON_SCHEDULE="0 7,19 * * *"
ENV TZ="UTC"

# Use our entrypoint script
ENTRYPOINT ["./entrypoint.sh"]