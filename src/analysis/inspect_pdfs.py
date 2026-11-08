import fitz
import pdfplumber
import pandas as pd

from pathlib import Path


# ==========================================
# CONFIGURATION
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parents[2]

PDF_DIR = PROJECT_DIR / "pdfs"

OUTPUT_DIR = Path(__file__).parent / "pdf_inspection_output"

OUTPUT_DIR.mkdir(exist_ok=True)


DOCUMENTS = {
    "10-K": "3M_2015_10K",
    "10-Q": "3M_2023Q2_10Q",
    "8-K": "BESTBUY_2023_8K_dated-2023-04-12",
    "Earnings": "BESTBUY_2023Q4_EARNINGS"
}


FINANCIAL_KEYWORDS = [
    "consolidated balance sheets",
    "consolidated statements of income",
    "consolidated statements of earnings",
    "consolidated statements of cash flows",
    "statements of cash flows",
    "balance sheets",
    "income statements",
    "cash flows"
]


# ==========================================
# HELPER: CLEAN TABLE
# ==========================================

def clean_table(table):

    df = pd.DataFrame(table)

    # Clean whitespace
    df = df.apply(
        lambda column: column.map(
            lambda x: x.strip() if isinstance(x, str) else x
        )
    )

    # Replace empty strings with None
    df = df.replace(r"^\s*$", None, regex=True)

    # Remove completely empty rows and columns
    df = df.dropna(axis=0, how="all")
    df = df.dropna(axis=1, how="all")

    return df


# ==========================================
# MAIN INSPECTION
# ==========================================

def inspect_pdfs():

    report_path = OUTPUT_DIR / "inspection_report.md"

    report = []

    report.append("# PDF Extraction Quality Inspection\n")

    print("=" * 70)
    print("TASK 1.3 — PDF EXTRACTION INSPECTION")
    print("=" * 70)

    for doc_type, doc_id in DOCUMENTS.items():

        pdf_path = PDF_DIR / f"{doc_id}.pdf"

        doc_output = OUTPUT_DIR / doc_id

        tables_dir = doc_output / "tables"

        tables_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 70}")
        print(f"DOCUMENT TYPE: {doc_type}")
        print(f"DOCUMENT ID: {doc_id}")
        print("=" * 70)

        report.append(f"\n# {doc_type}: {doc_id}\n")

        if not pdf_path.exists():

            print("ERROR: PDF not found")

            report.append("PDF not found.\n")

            continue

        # ==================================
        # 1. PYMUPDF TEXT EXTRACTION
        # ==================================

        pdf = fitz.open(pdf_path)

        total_pages = len(pdf)

        print(f"Total PDF Pages: {total_pages}")

        report.append(f"- Total physical pages: {total_pages}\n")

        sample_pages = {
            0,
            min(2, total_pages - 1),
            total_pages // 2,
            total_pages - 1
        }

        extracted_text = []

        financial_pages = []

        for page_num, page in enumerate(pdf):

            text = page.get_text("text")

            if not text.strip():

                continue

            extracted_text.append(
                f"\n{'=' * 60}\n"
                f"PHYSICAL PAGE: {page_num + 1}\n"
                f"{'=' * 60}\n"
                f"{text}"
            )

            # Detect financial statement pages
            matched_keywords = [
                keyword
                for keyword in FINANCIAL_KEYWORDS
                if keyword in text.lower()
            ]

            if matched_keywords:

                financial_pages.append(page_num + 1)

            # Inspect selected sample pages
            if page_num in sample_pages:

                print(f"\nPhysical Page: {page_num + 1}")
                print(f"Characters: {len(text)}")

                print("\nText Preview:")
                print(text[:1000])

                report.append(
                    f"\n## Physical Page {page_num + 1}\n"
                )

                report.append(
                    f"- Extracted characters: {len(text)}\n"
                )

                report.append(
                    f"- Text preview:\n\n{text[:1000]}\n"
                )

        # Save complete extracted text
        text_path = doc_output / "extracted_text.txt"

        with open(text_path, "w", encoding="utf-8") as file:

            file.write("\n".join(extracted_text))

        print(f"\nSaved text: {text_path}")

        report.append(
            f"\n- Financial statement keyword pages: "
            f"{financial_pages}\n"
        )

        pdf.close()

        # ==================================
        # 2. PDFPLUMBER TABLE EXTRACTION
        # ==================================

        print("\n--- TABLE EXTRACTION ---")

        total_tables = 0

        table_pages = []

        with pdfplumber.open(pdf_path) as pdf:

            for page_num, page in enumerate(
                pdf.pages, start=1
            ):

                tables = page.extract_tables()

                for table_num, table in enumerate(
                    tables, start=1
                ):

                    if not table:
                        continue

                    df = clean_table(table)

                    if df.empty:
                        continue

                    total_tables += 1

                    table_pages.append(page_num)

                    table_file = (
                        tables_dir /
                        f"page_{page_num}_table_{table_num}.csv"
                    )

                    df.to_csv(
                        table_file,
                        index=False,
                        header=False
                    )

                    # Print table preview
                    print(
                        f"\nPage: {page_num} | "
                        f"Table: {table_num}"
                    )

                    print(df.to_string(
                        index=False,
                        header=False
                    ))

                    print("-" * 60)

        print(f"\nTotal extracted tables: {total_tables}")

        print(f"Pages containing tables: {sorted(set(table_pages))}")

        report.append("\n## Table Extraction\n")

        report.append(
            f"- Total extracted tables: {total_tables}\n"
        )

        report.append(
            f"- Pages containing tables: "
            f"{sorted(set(table_pages))}\n"
        )

        report.append(
            f"- Table CSV files saved in: {tables_dir}\n"
        )

        print("\nDocument inspection completed.")

    # ======================================
    # 3. SAVE FINAL REPORT
    # ======================================

    with open(report_path, "w", encoding="utf-8") as file:

        file.writelines(report)

    print("\n" + "=" * 70)
    print("ALL PDF INSPECTIONS COMPLETED")
    print("=" * 70)

    print(f"\nInspection report: {report_path}")


# ==========================================
# EXECUTION
# ==========================================

if __name__ == "__main__":

    inspect_pdfs()