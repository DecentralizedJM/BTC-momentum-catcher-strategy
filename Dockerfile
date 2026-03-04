# Use official slim python image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create and set working directory
WORKDIR /app

# Install git and other build dependencies (required for mudrex SDK install from github)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file explicitly
COPY requirements.txt .

# Install all python dependencies, including Mudrex SDK
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir git+https://github.com/DecentralizedJM/mudrex-api-trading-python-sdk.git

# Copy existing project source code into container
COPY bot.py .
COPY mudrex_executor.py .
COPY README.md .

# Define execution entry point for Railway
CMD ["python", "bot.py"]
