import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')

# Read data locally
df = pd.read_csv(r'c:\DBMS PROJECT\etl\seller_features.csv')

print(f"Dataset shape: {df.shape}")
df.head()

print("=== Missing Values ===")
print(df.isnull().sum())
print(f"\n=== Dataset Info ===")
print(f"Sellers: {len(df)}")
print(f"Features: {df.shape[1]}")

# Show basic statistics
df.describe()

plt.figure(figsize=(10, 8))
sns.heatmap(df.select_dtypes(include=[np.number]).corr(), annot=False, cmap='coolwarm')
plt.title('Correlation Heatmap')
plt.close()

fig, axes = plt.subplots(1, 2, figsize=(15, 5))
sns.histplot(df['total_orders'], bins=50, ax=axes[0])
axes[0].set_title('Distribution of Total Orders')
sns.histplot(df['avg_review_score'], bins=20, ax=axes[1])
axes[1].set_title('Distribution of Avg Review Score')
plt.close()

# Create a composite risk score from key metrics
# Normalize each metric to 0-1 scale
from sklearn.preprocessing import MinMaxScaler

feature_cols = ['total_orders', 'total_items', 'unique_products',
                'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
                'avg_review_score', 'rejection_rate', 'negative_review_count',
                'avg_price', 'price_volatility', 'avg_freight']

# Fill NaN with 0 for numeric features
df[feature_cols] = df[feature_cols].fillna(0)

# Create composite score for labeling (higher = more risky)
# Invert avg_review_score since higher is better
df['composite_score'] = (
    df['pct_late_deliveries'].rank(pct=True) * 0.3 +
    (1 - df['avg_review_score'].rank(pct=True)) * 0.3 +
    df['rejection_rate'].rank(pct=True) * 0.2 +
    df['price_volatility'].rank(pct=True) * 0.2
)

# Assign risk categories based on data-driven percentile thresholds
df['RiskCategory'] = pd.cut(
    df['composite_score'],
    bins=[-np.inf, df['composite_score'].quantile(0.50),
          df['composite_score'].quantile(0.80), np.inf],
    labels=['Low', 'Medium', 'High']
)

print("Risk Category Distribution:")
print(df['RiskCategory'].value_counts())

plt.figure(figsize=(8, 5))
sns.countplot(x='RiskCategory', data=df, order=['Low', 'Medium', 'High'])
plt.title('Risk Category Distribution')
plt.close()

# Encode target
le = LabelEncoder()
y = le.fit_transform(df['RiskCategory'])

# Feature matrix
X = df[feature_cols].copy()

# Train/test split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f"Training set: {X_train.shape[0]} samples")
print(f"Test set: {X_test.shape[0]} samples")

# Train XGBoost - tree_method='hist' works on both CPU and GPU
# Note: T4 GPU is optional; dataset is small and trains fast on CPU
model = xgb.XGBClassifier(
    n_estimators=200,
    max_depth=6,
    learning_rate=0.1,
    tree_method='hist',  # Works on CPU and GPU
    objective='multi:softmax',
    num_class=3,
    eval_metric='mlogloss',
    random_state=42
)

model.fit(
    X_train, y_train,
    eval_set=[(X_test, y_test)],
    verbose=20
)

y_pred = model.predict(X_test)

print("=== Classification Report ===")
print(classification_report(y_test, y_pred, target_names=le.classes_))

print(f"\nAccuracy: {accuracy_score(y_test, y_pred):.4f}")
print(f"F1 Score (macro): {f1_score(y_test, y_pred, average='macro'):.4f}")

# Confusion Matrix
fig, axes = plt.subplots(1, 2, figsize=(16, 5))

cm = confusion_matrix(y_test, y_pred)
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=le.classes_, yticklabels=le.classes_, ax=axes[0])
axes[0].set_title('Confusion Matrix')
axes[0].set_xlabel('Predicted')
axes[0].set_ylabel('Actual')

# Feature Importance
xgb.plot_importance(model, ax=axes[1], max_num_features=12)
axes[1].set_title('Feature Importance')

plt.tight_layout()
plt.close()

# Save model
model.save_model(r'c:\DBMS PROJECT\training\vendor_risk_model.json')
print("Model saved to vendor_risk_model.json")

# Also save the label encoder classes for inference
import json
with open(r'c:\DBMS PROJECT\training\label_classes.json', 'w') as f:
    json.dump(le.classes_.tolist(), f)
print("Label classes saved to label_classes.json")