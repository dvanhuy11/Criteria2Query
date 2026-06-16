"""
Script setup: Import OMOP Vocabulary vào SQLite database
=========================================================
Chạy script này SAU KHI tải vocabulary files từ Athena.

Cách dùng:
    python setup_vocab_db.py --vocab-dir /path/to/vocabulary_files

Vocabulary files cần thiết (tải từ https://athena.ohdsi.org):
    - CONCEPT.csv
    - CONCEPT_RELATIONSHIP.csv (tuỳ chọn)
    - CONCEPT_ANCESTOR.csv (tuỳ chọn)

Output: vocab.sqlite (đặt cùng thư mục với concepthub_server.py)
"""

import sqlite3
import csv
import os
import argparse
import sys

def setup_database(vocab_dir: str, output_db: str):
    concept_file = os.path.join(vocab_dir, "CONCEPT.csv")

    if not os.path.exists(concept_file):
        print(f"[ERROR] Không tìm thấy: {concept_file}")
        print("Hãy tải vocabulary files từ https://athena.ohdsi.org")
        sys.exit(1)

    print(f"[INFO] Đang tạo database: {output_db}")
    conn = sqlite3.connect(output_db)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS concept")
    cur.execute("""
        CREATE TABLE concept (
            concept_id       INTEGER PRIMARY KEY,
            concept_name     TEXT NOT NULL,
            domain_id        TEXT,
            vocabulary_id    TEXT,
            concept_class_id TEXT,
            standard_concept TEXT,
            concept_code     TEXT,
            valid_start_date TEXT,
            valid_end_date   TEXT,
            invalid_reason   TEXT
        )
    """)

    print(f"[INFO] Đang đọc {concept_file} ...")
    count = 0
    with open(concept_file, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        batch = []
        for row in reader:
            batch.append((
                int(row["concept_id"]),
                row["concept_name"],
                row["domain_id"],
                row["vocabulary_id"],
                row["concept_class_id"],
                row.get("standard_concept", ""),
                row["concept_code"],
                row.get("valid_start_date", ""),
                row.get("valid_end_date", ""),
                row.get("invalid_reason", "")
            ))
            count += 1
            if len(batch) >= 10000:
                cur.executemany("INSERT OR IGNORE INTO concept VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                batch = []
                print(f"  Đã import: {count:,} concepts...", end="\r")

        if batch:
            cur.executemany("INSERT OR IGNORE INTO concept VALUES (?,?,?,?,?,?,?,?,?,?)", batch)

    print(f"\n[INFO] Tổng cộng: {count:,} concepts")

    print("[INFO] Đang tạo index...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_name ON concept(concept_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_domain ON concept(domain_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_standard ON concept(standard_concept)")

    conn.commit()
    conn.close()
    print(f"[SUCCESS] Database đã sẵn sàng: {output_db}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup OMOP Vocabulary SQLite DB")
    parser.add_argument("--vocab-dir", required=True,
                        help="Thư mục chứa CONCEPT.csv (tải từ Athena)")
    parser.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "vocab.sqlite"),
                        help="Đường dẫn file output SQLite")
    args = parser.parse_args()

    setup_database(args.vocab_dir, args.output)
