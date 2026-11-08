import pandas as pd
import csv
from pathlib import Path

# Manifest path
manifest_path = Path(r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\manifest.csv")

# Detect actual delimiter
with open(manifest_path, "r", encoding="utf-8-sig") as file:
    sample = file.read(8192)

dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
delimiter = dialect.delimiter

print("=" * 60)
print("TASK 1.2 — MANIFEST ANALYSIS")
print("=" * 60)

print(f"\nDetected delimiter: {repr(delimiter)}")

# Read manifest without modifying doc_id values
df = pd.read_csv(
    manifest_path,
    sep=delimiter,
    dtype=str,
    keep_default_na=False,
    encoding="utf-8-sig"
)

print("\nColumns:", df.columns.tolist())

# Basic information
print("\nTotal documents:", len(df))

# Unique companies
print("\nUnique companies:", df["company"].nunique())

# Documents per company
print("\n--- Documents Per Company ---")
print(df["company"].value_counts().to_string())

# Documents per desk
print("\n--- Documents Per Desk ---")
print(df["desk"].value_counts().to_string())

# Document type distribution
print("\n--- Document Type Distribution ---")
print(df["doc_type"].value_counts().to_string())

# Reporting period distribution
print("\n--- Reporting Period Distribution ---")
print(df["period"].value_counts().to_string())

# Unique document IDs
print("\n--- Document ID Validation ---")
print("Unique doc_ids:", df["doc_id"].nunique())

duplicates = df[df["doc_id"].duplicated(keep=False)]

print("Duplicate doc_ids:", len(duplicates))

if not duplicates.empty:
    print(duplicates["doc_id"].to_string(index=False))

print("\n" + "=" * 60)
print("MANIFEST ANALYSIS COMPLETED")
print("=" * 60)