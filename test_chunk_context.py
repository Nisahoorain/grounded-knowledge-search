from src.ingestion.chunker import chunk_extracted_file

p = "data/extracted/PEPSICO_2021_10K.json"
chunks = chunk_extracted_file(p)

for x in chunks:
    if (
        x["content_type"] == "table"
        and x["page"] in (53, 63)
        and "Capital spending" in x["text"]
    ):
        print(f"PAGE {x['page']}:")
        print(x["text"])
        print("-" * 60)
