
import json
import re
import urllib.request
import urllib.error

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2:latest"


def generate_answer(question, retrieved_chunks):
    if not retrieved_chunks:
        return {
            "status": "refused",
            "answer": "The provided filings do not contain sufficient evidence to answer this question.",
            "citations": [],
        }

    evidence = []
    citation_map = {}

    for i, item in enumerate(retrieved_chunks, start=1):
        ref = f"E{i}"
        citation_map[ref] = {
            "doc_id": item["doc_id"],
            "page": item["page"],
            "chunk_id": item["chunk_id"],
        }
        evidence.append(
            f"[{ref}] Document: {item['doc_id']}, "
            f"Page: {item['page']}\n{item['text']}"
        )

    prompt = f"""
You are a financial filing question-answering system.

Answer the question using ONLY the evidence below.

Rules:
1. Never use outside knowledge.
2. Do not invent facts or figures.
3. Cite every factual claim using evidence IDs like [E1].
4. If evidence is insufficient, reply exactly:
INSUFFICIENT_EVIDENCE
5. Preserve units and reporting periods.
6. Be concise.

Evidence:
{chr(10).join(evidence)}

Question: {question}

Answer:
"""

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))

        answer = result["message"]["content"].strip()

    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Ollama answer generation failed: {exc}") from exc

    if "INSUFFICIENT_EVIDENCE" in answer.upper():
        return {
            "status": "refused",
            "answer": "The provided filings do not contain sufficient evidence to answer this question.",
            "citations": [],
        }

    used_refs = list(dict.fromkeys(
        re.findall(r"\[(E\d+)\]", answer)
    ))

    valid_refs = [
        ref for ref in used_refs
        if ref in citation_map
    ]

    if not valid_refs:
        return {
            "status": "refused",
            "answer": "The system could not produce an answer with verifiable citations.",
            "citations": [],
        }

    citations = [citation_map[ref] for ref in valid_refs]

    return {
        "status": "answered",
        "answer": answer,
        "citations": citations,
    }
