import json
import os
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(PROJECT_ROOT, 'etl', 'seller_features.csv')
MODEL_PATH = os.path.join(PROJECT_ROOT, 'training', 'vendor_risk_model.json')
LABELS_PATH = os.path.join(PROJECT_ROOT, 'training', 'label_classes.json')

df = pd.read_csv(DATA_PATH)

print(f"Dataset shape: {df.shape}")
print("=== Missing Values ===")
print(df.isnull().sum())
print(f"\n=== Dataset Info ===")
print(f"Sellers: {len(df)}")
print(f"Features: {df.shape[1]}")

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

feature_cols = [
    'total_orders', 'total_items', 'unique_products',
    'avg_delay_days', 'max_delay_days', 'pct_late_deliveries',
    'avg_review_score', 'rejection_rate', 'negative_review_count',
    'avg_price', 'price_volatility', 'avg_freight', 'defect_penalty',
]

if 'defect_penalty' not in df.columns:
    df['defect_penalty'] = 0.0

df[feature_cols] = df[feature_cols].fillna(0)

df['composite_score'] = (
    df['pct_late_deliveries'].rank(pct=True) * 0.3
    + (1 - df['avg_review_score'].rank(pct=True)) * 0.3
    + df['rejection_rate'].rank(pct=True) * 0.2
    + df['price_volatility'].rank(pct=True) * 0.1
    + df['defect_penalty'].rank(pct=True) * 0.1
)

df['RiskCategory'] = pd.cut(
    df['composite_score'],
    bins=[-np.inf, df['composite_score'].quantile(0.50),
          df['composite_score'].quantile(0.80), np.inf],
    labels=['Low', 'Medium', 'High'],
)

print("Risk Category Distribution:")
print(df['RiskCategory'].value_counts())

plt.figure(figsize=(8, 5))
sns.countplot(x='RiskCategory', data=df, order=['Low', 'Medium', 'High'])
plt.title('Risk Category Distribution')
plt.close()

le = LabelEncoder()
y = le.fit_transform(df['RiskCategory'])
X = df[feature_cols].copy()

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f"Training set: {X_train.shape[0]} samples")
print(f"Test set: {X_test.shape[0]} samples")

model = xgb.XGBClassifier(
    n_estimators=200,
    max_depth=6,
    learning_rate=0.1,
    tree_method='hist',
    objective='multi:softmax',
    num_class=3,
    eval_metric='mlogloss',
    random_state=42,
)

model.fit(
    X_train, y_train,
    eval_set=[(X_test, y_test)],
    verbose=20,
)

y_pred = model.predict(X_test)

print("=== Classification Report ===")
print(classification_report(y_test, y_pred, target_names=le.classes_))

print(f"\nAccuracy: {accuracy_score(y_test, y_pred):.4f}")
print(f"F1 Score (macro): {f1_score(y_test, y_pred, average='macro'):.4f}")

fig, axes = plt.subplots(1, 2, figsize=(16, 5))

cm = confusion_matrix(y_test, y_pred)
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=le.classes_, yticklabels=le.classes_, ax=axes[0])
axes[0].set_title('Confusion Matrix')
axes[0].set_xlabel('Predicted')
axes[0].set_ylabel('Actual')

xgb.plot_importance(model, ax=axes[1], max_num_features=13)
axes[1].set_title('Feature Importance')

plt.tight_layout()
plt.close()

model.save_model(MODEL_PATH)
print(f"Model saved to {MODEL_PATH}")

with open(LABELS_PATH, 'w', encoding='utf-8') as f:
    json.dump(le.classes_.tolist(), f)
print(f"Label classes saved to {LABELS_PATH}")
