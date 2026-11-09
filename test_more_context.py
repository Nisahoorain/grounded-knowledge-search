from src.ingestion.chunker import chunk_extracted_file

tests = [
    ("BOEING_2018_10K.json", 52),
    ("BESTBUY_2023_10K.json", 42),
]

for filename, page in tests:
    print(f"\n{'='*20} {filename} PAGE {page} {'='*20}")
    chunks = chunk_extracted_file("data/extracted/" + filename)

    rows = [
        x for x in chunks
        if x["content_type"] == "table" and x["page"] == page
    ]

    print("Table chunks:", len(rows))

    for row in rows[:2]:
        print(row["text"])
        print("-" * 50)
