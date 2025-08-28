# Warning Work In Progress - From ChatGPT
# Use official Python image as base
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Copy your script into the container
COPY CheckRoyalCaribbeanPrice.py .

# If you have requirements, uncomment the next lines:
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Allow passing args at runtime (ENTRYPOINT + CMD pattern)
ENTRYPOINT ["python", "CheckRoyalCaribbeanPrice.py"]
