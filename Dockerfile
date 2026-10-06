FROM python:3.11-slim

WORKDIR /app

# Copy dependencies first for Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project (Dockerignore will exclude heavy unused files)
COPY . .

# Set environment variables for production
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Expose the port Cloud Run uses
EXPOSE 8080

# Run gunicorn pointing to the Flask app inside desktop_app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "0", "flask_webapp.app:app"]
