from pyspark.sql import SparkSession

# Initialize Spark session
spark = SparkSession.builder.appName("DataPipeline").getOrCreate()

# Data ingestion and preprocessing
data = [("Narender", 1), ("Aarush", 2)]
columns = ["Name", "ID"]
df = spark.createDataFrame(data, columns)

# Save preprocessed data to S3
s3_bucket = "s3a://your-bucket-name/preprocessed_data"
df.write.csv(s3_bucket)

print("Data preprocessed and saved to S3")

spark.stop()
