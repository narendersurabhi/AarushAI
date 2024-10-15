import joblib
from sklearn.metrics import classification_report
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

# Load dataset
iris = load_iris()
X_train, X_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)

# Load the trained model
model = joblib.load('../models/model.pkl')

# Make predictions
predictions = model.predict(X_test)

# Evaluate the model
report = classification_report(y_test, predictions)
print(report)

# Save evaluation report
with open('../models/metrics/evaluation_report.txt', 'w') as f:
    f.write(report)

print("Model evaluation complete and report saved.")
