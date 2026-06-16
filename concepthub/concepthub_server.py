"""
Concept Hub Server for Criteria2Query
======================================
Server này đóng vai trò trung gian giữa C2Q (Java) và Usagi.
C2Q gửi POST request với {term, domain} → server này gọi Usagi → trả về concept.

Cách chạy:
    python concepthub_server.py

Yêu cầu:
    - Usagi đã được cài và đang chạy (hoặc đã build index, xem README bên dưới)
    - Flask: pip install flask requests

Cổng mặc định: 8081 (khớp với GlobalSetting.java)
"""

from flask import Flask, request, jsonify
import sqlite3
import os
import json

app = Flask(__name__)

# =====================================================================
# CẤU HÌNH - Thay đổi đường dẫn tới Usagi index của bạn
# =====================================================================
# Sau khi chạy Usagi và build index, nó tạo ra một thư mục index
# Mặc định Usagi lưu ở: ~/.usagi/index/
USAGI_INDEX_PATH = os.path.expanduser("~/.usagi/index")

# Nếu bạn muốn dùng Usagi HTTP API trực tiếp, đặt URL ở đây
# (Usagi có thể chạy ở chế độ server, xem README)
USAGI_API_URL = None  # ví dụ: "http://localhost:9090"

# =====================================================================
# DOMAIN MAPPING: C2Q domain → OMOP CDM domain
# =====================================================================
DOMAIN_MAP = {
    "Condition":    "Condition",
    "Drug":         "Drug",
    "Measurement":  "Measurement",
    "Procedure":    "Procedure",
    "Observation":  "Observation",
    "Demographic":  "Observation",
    "Visit":        "Visit",
    "Device":       "Device",
}


def search_concept_via_usagi_api(term: str, domain: str) -> dict:
    """
    Gọi Usagi REST API để tìm concept.
    Dùng khi Usagi đang chạy ở chế độ server.
    """
    import requests
    omop_domain = DOMAIN_MAP.get(domain, domain)
    payload = {"term": term, "domain": omop_domain}
    try:
        resp = requests.post(f"{USAGI_API_URL}/omop/searchOneEntityByTermAndDomain",
                             json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e), "matchScore": 0, "concept": {"conceptId": 0}}


def search_concept_local(term: str, domain: str) -> dict:
    """
    Tìm concept từ Lucene index của Usagi (nếu không chạy Usagi server).
    Đây là fallback đơn giản dùng SQLite nếu bạn đã export vocabulary ra DB.

    Để dùng tính năng này:
    1. Chạy script setup_vocab_db.py để tạo vocab.sqlite từ OMOP vocabulary files
    2. Đặt đường dẫn vào VOCAB_DB_PATH bên dưới
    """
    VOCAB_DB_PATH = os.path.join(os.path.dirname(__file__), "vocab.sqlite")

    if not os.path.exists(VOCAB_DB_PATH):
        # Trả về kết quả giả nếu chưa có DB - để test C2Q chạy được trước
        return {
            "matchScore": 0.0,
            "concept": {
                "conceptId": 0,
                "conceptName": f"[UNMAPPED] {term}",
                "domainId": domain,
                "vocabularyId": "None",
                "conceptClassId": "None",
                "standardConcept": None,
                "conceptCode": "0",
                "validStartDate": "1970-01-01",
                "validEndDate": "2099-12-31",
                "invalidReason": None
            }
        }

    omop_domain = DOMAIN_MAP.get(domain, domain)
    conn = sqlite3.connect(VOCAB_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Tìm exact match trước
    cur.execute("""
        SELECT concept_id, concept_name, domain_id, vocabulary_id,
               concept_class_id, standard_concept, concept_code
        FROM concept
        WHERE LOWER(concept_name) = LOWER(?)
          AND domain_id = ?
          AND standard_concept = 'S'
        LIMIT 1
    """, (term, omop_domain))
    row = cur.fetchone()

    if not row:
        # Tìm LIKE match
        cur.execute("""
            SELECT concept_id, concept_name, domain_id, vocabulary_id,
                   concept_class_id, standard_concept, concept_code
            FROM concept
            WHERE LOWER(concept_name) LIKE LOWER(?)
              AND domain_id = ?
              AND standard_concept = 'S'
            ORDER BY LENGTH(concept_name) ASC
            LIMIT 1
        """, (f"%{term}%", omop_domain))
        row = cur.fetchone()

    conn.close()

    if row:
        return {
            "matchScore": 1.0 if row["concept_name"].lower() == term.lower() else 0.7,
            "concept": {
                "conceptId": row["concept_id"],
                "conceptName": row["concept_name"],
                "domainId": row["domain_id"],
                "vocabularyId": row["vocabulary_id"],
                "conceptClassId": row["concept_class_id"],
                "standardConcept": row["standard_concept"],
                "conceptCode": row["concept_code"],
                "validStartDate": "1970-01-01",
                "validEndDate": "2099-12-31",
                "invalidReason": None
            }
        }
    else:
        return {
            "matchScore": 0.0,
            "concept": {
                "conceptId": 0,
                "conceptName": f"[UNMAPPED] {term}",
                "domainId": domain,
                "vocabularyId": "None",
                "conceptClassId": "None",
                "standardConcept": None,
                "conceptCode": "0",
                "validStartDate": "1970-01-01",
                "validEndDate": "2099-12-31",
                "invalidReason": None
            }
        }


# =====================================================================
# API ENDPOINTS
# =====================================================================

@app.route("/concepthub", methods=["POST"])
def concepthub_main():
    """
    Endpoint chính - C2Q gọi POST /concepthub với body: {"term": ..., "domain": ...}
    """
    data = request.get_json(force=True) or {}
    term   = data.get("term", "")
    domain = data.get("domain", "Condition")

    print(f"[ConceptHub] Searching: term='{term}', domain='{domain}'")

    if USAGI_API_URL:
        result = search_concept_via_usagi_api(term, domain)
    else:
        result = search_concept_local(term, domain)

    print(f"[ConceptHub] Result: conceptId={result.get('concept', {}).get('conceptId')}, "
          f"score={result.get('matchScore')}")
    return jsonify(result)


@app.route("/concepthub/umls/searchUMLS", methods=["POST"])
def search_umls():
    """
    UMLS search endpoint - C2Q dùng để mở rộng abbreviations
    """
    data = request.get_json(force=True) or {}
    term = data.get("term", "")

    # Stub đơn giản - trả về term gốc không thay đổi
    # Để dùng thực sự, cần UMLS API key từ https://uts.nlm.nih.gov/uts/
    return jsonify({
        "term": term,
        "expanded": term,
        "source": "stub"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "ConceptHub is running"})


if __name__ == "__main__":
    print("=" * 60)
    print("  Concept Hub Server for Criteria2Query")
    print("=" * 60)
    print(f"  Listening on: http://localhost:8081")
    print(f"  Usagi API URL: {USAGI_API_URL or 'Not configured (using local DB)'}")
    print(f"  Vocab DB: {os.path.join(os.path.dirname(__file__), 'vocab.sqlite')}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8081, debug=True)
