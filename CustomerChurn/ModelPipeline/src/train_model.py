import json
import joblib
import boto3
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

# Load configuration
with open('config/algorithms.json') as f:
    algorithms = json.load(f)

# Load dataset
iris = load_iris()
X_train, X_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)

s3 = boto3.client('s3')
bucket_name = 'your-bucket-name'

def save_model_to_s3(model, model_name):
    model_path = f"../models/{model_name}.pkl"
    joblib.dump(model, model_path)
    s3.upload_file(model_path, bucket_name, f"models/{model_name}.pkl")
    print(f"Model {model_name} saved to S3.")

def train_random_forest(params):
    model = RandomForestClassifier(**params)
    model.fit(X_train, y_train)
    save_model_to_s3(model, 'random_forest_model')

def train_svm(params):
    model = SVC(**params)
    model.fit(X_train, y_train)
    save_model_to_s3(model, 'svm_model')

# Train models in parallel
from multiprocessing import Process

if __name__ == "__main__":
    processes = []
    
    for algo in algorithms:
        if algo['name'] == 'RandomForest':
            p = Process(target=train_random_forest, args=(algo['parameters'],))
        elif algo['name'] == 'SVM':
            p = Process(target=train_svm, args=(algo['parameters'],))
        processes.append(p)
        p.start()
    
    for p in processes:
        p.join()

    print("Model training complete and saved.")
