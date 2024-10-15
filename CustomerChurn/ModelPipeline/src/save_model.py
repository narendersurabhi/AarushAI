import joblib

# Example function to save a model
def save_model(model, model_path):
    joblib.dump(model, model_path)
    print(f"Model saved to {model_path}")

# Example usage
if __name__ == "__main__":
    model = joblib.load('../models/model.pkl')
    save_model(model, '../models/model_version_2.pkl')
