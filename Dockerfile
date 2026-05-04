# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for OpenCV and DICOM processing
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Create a non-root user (Hugging Face Spaces requirement)
RUN useradd -m -u 1000 user

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create necessary directories and set permissions for the non-root user
RUN mkdir -p uploads logs instance \
    && chown -R user:user /app \
    && chmod -R 755 /app \
    && chmod +x start.sh

# Switch to the non-root user
USER user

# Expose the port Hugging Face Spaces uses
EXPOSE 7860

# Run the startup script
CMD ["./start.sh"]
