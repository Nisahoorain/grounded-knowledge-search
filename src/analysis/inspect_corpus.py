import os
import json
import pandas as pd

# Dataset paths
PDF_DIR = r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\pdfs"
MANIFEST_PATH = r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\manifest.csv"
QUESTIONS_PATH = r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\dev_questions.jsonl"


def inspect_corpus():

    print("\n========== DATASET INSPECTION ==========\n")

    # 1. Check dataset files
    print("1. Checking dataset files...")

    for path in [PDF_DIR, MANIFEST_PATH, QUESTIONS_PATH]:
        print(f"{path}: {'FOUND' if os.path.exists(path) else 'MISSING'}")

    if not os.path.isdir(PDF_DIR):
        raise FileNotFoundError("PDF directory not found.")

    if not os.path.isfile(MANIFEST_PATH):
        raise FileNotFoundError("Manifest file not found.")

    if not os.path.isfile(QUESTIONS_PATH):
        raise FileNotFoundError("Questions file not found.")

    # 2. Inspect PDF files
    print("\n2. Inspecting PDF files...")

    pdf_files = [
        file for file in os.listdir(PDF_DIR)
        if file.lower().endswith(".pdf")
    ]

    print(f"Total PDF files: {len(pdf_files)}")

    # 3. Load manifest
    print("\n3. Loading manifest...")

    manifest = pd.read_csv(MANIFEST_PATH, sep=None, engine="python")

    print(f"Total manifest records: {len(manifest)}")
    print(f"Columns: {list(manifest.columns)}")

    required_columns = {
        "doc_id", "company", "sector",
        "desk", "doc_type", "period"
    }

    missing_columns = required_columns - set(manifest.columns)

    if missing_columns:
        raise ValueError(f"Missing manifest columns: {missing_columns}")

    # 4. Verify PDF and manifest mapping
    print("\n4. Verifying PDF and manifest mapping...")

    pdf_ids = {
        os.path.splitext(file)[0]
        for file in pdf_files
    }

    manifest_ids = set(manifest["doc_id"].astype(str))

    missing_pdfs = manifest_ids - pdf_ids
    untracked_pdfs = pdf_ids - manifest_ids

    print(f"Manifest documents without PDF: {len(missing_pdfs)}")
    print(f"PDFs missing from manifest: {len(untracked_pdfs)}")

    if missing_pdfs:
        print("Missing PDFs:", sorted(missing_pdfs))

    if untracked_pdfs:
        print("Untracked PDFs:", sorted(untracked_pdfs))

    # 5. Analyze companies and desks
    print("\n5. Company distribution:")

    print(manifest["company"].value_counts().to_string())

    print("\nDesk distribution:")

    print(manifest["desk"].value_counts().to_string())

    print("\nDocument type distribution:")

    print(manifest["doc_type"].value_counts().to_string())

    print("\nReporting period distribution:")

    print(manifest["period"].value_counts().sort_index().to_string())

    # 6. Inspect development questions
    print("\n6. Inspecting development questions...")

    questions = []

    with open(QUESTIONS_PATH, "r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                questions.append(json.loads(line))

    print(f"Total development questions: {len(questions)}")

    print("\nQuestions by role:")

    question_df = pd.DataFrame(questions)

    print(question_df["role"].value_counts().to_string())

    # 7. Final summary
    print("\n========== INSPECTION SUMMARY ==========")

    print(f"PDF files: {len(pdf_files)}")
    print(f"Manifest records: {len(manifest)}")
    print(f"Companies: {manifest['company'].nunique()}")
    print(f"Desks: {manifest['desk'].nunique()}")
    print(f"Development questions: {len(questions)}")

    print("\nInspection completed.")


if __name__ == "__main__":
    inspect_corpus()
