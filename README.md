# Supply Chain Vendor Risk Scoring System

This project is a comprehensive database and machine learning application designed to evaluate, predict, and monitor vendor risk in a supply chain context.

## Overview
The system uses historical e-commerce data (from Olist datasets) to compute vendor risk scores based on four key metrics:
1. **Delivery Delay**
2. **Quality (Review Scores)**
3. **Price Volatility**
4. **Defect Occurrences (NLP)**

These scores are aggregated into a `WeightedRiskScore` and each seller is assigned a `RiskCategory` (Low, Medium, High). Additionally, the system includes a trained XGBoost model that can dynamically predict a vendor's risk category based on live features.

## Architecture & Components
- **Oracle Database (`db/`)**: The core relational database storing all sellers, products, purchase orders, quality inspections, and calculated risk scores.
- **ETL Pipeline (`etl/`)**: Scripts to load raw CSV data into Oracle, extract seller features, and import NLP-processed review sentiments.
- **NLP Processing (`nlp/`)**: Scripts and Jupyter notebooks using VADER sentiment analysis and zero-shot classification to categorize negative reviews into specific defect types (e.g., "Late Delivery", "Damaged Goods").
- **Machine Learning (`training/`)**: An XGBoost model trained on extracted seller features to predict risk categories.
- **Desktop Application (`desktop_app/`)**: A cross-platform GUI built with Flask and PyWebView. It acts as the interactive dashboard for the user to search for sellers, view live ML predictions, and review alerts.

## How to Run

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Launch the Desktop App**
   Navigate to `desktop_app/` and run the main application:
   ```bash
   python main.py
   ```
   The application will ask for your Oracle Database credentials on startup. It will also bootstrap the required database schema automatically if it detects an empty database.

## Distribution

The application can be compiled into a standalone desktop executable using PyInstaller. A `.spec` file is included in `desktop_app/`.
All build artifacts (like `build/` and `dist/`) are intentionally excluded from version control to save space. To build it yourself:

```bash
cd desktop_app
py -m PyInstaller --name SupplyChainScorer --windowed --add-data "schema_setup.sql;." --add-data "templates;templates" --add-data "app.py;." --add-data "..\training\vendor_risk_model.json;training" --add-data "..\training\label_classes.json;training" --hidden-import cryptography --hidden-import cryptography.hazmat.primitives.kdf.pbkdf2 --collect-all xgboost main.py -y
```

The resulting application will be available in `desktop_app/dist/SupplyChainScorer`.
