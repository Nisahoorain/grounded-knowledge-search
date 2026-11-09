from src.ingestion.chunker import chunk_extracted_file

p = "data/extracted/3M_2015_10K.json"
chunks = chunk_extracted_file(p)

for page in (50, 52, 54):
    print(f"\n{'='*20} PAGE {page} {'='*20}")
    rows = [
        x for x in chunks
        if x["content_type"] == "table" and x["page"] == page
    ]

    print("Table chunks:", len(rows))

    for row in rows[:2]:
        print(row["text"])
        print("-" * 50)
