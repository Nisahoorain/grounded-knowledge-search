import pdfplumber
import pandas as pd

def extract_clean_table(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # Extract tables using default or explicit settings
            tables = page.extract_tables()
            
            for table in tables:
                if table:
                    # Convert to DataFrame
                    df = pd.DataFrame(table)
                    
                    # 1. Drop columns that are completely empty (the extra blank columns)
                    df = df.dropna(axis=1, how='all')
                    
                    # 2. Drop rows that are completely empty
                    df = df.dropna(axis=0, how='all')
                    
                    # 3. Clean up whitespace in cells
                    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
                    
                    print(f"--- Cleaned Table from Page {page_num} ---")
                    print(df.to_string(index=False, header=False))
                    print("\n" + "="*50 + "\n")

if __name__ == "__main__":
    extract_clean_table(r"C:\Users\QueOne\OneDrive\Desktop\grounded-knowledge-search\pdfs\CORNING_2022_10K.pdf" )
