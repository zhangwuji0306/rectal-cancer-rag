# -*- coding: utf-8 -*-
import csv, re, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
rows = list(csv.DictReader(open(r"E:\writing-rag\papers\索引信息.csv", encoding="utf-8-sig")))
fa = rows[0]["File Attachments"]
print("fa repr:", repr(fa[:120]))
pat1 = re.compile("storage\\\\/([A-Za-z0-9]{8})")       # literal backslash+slash
pat2 = re.compile(re.escape("storage\\") + r"([A-Za-z0-9]{8})")
pat3 = re.compile(r"storage[\\]/")                       # char class backslash or slash
print("pat1:", pat1.pattern, "->", pat1.findall(fa))
print("pat2:", pat2.pattern, "->", pat2.findall(fa))
print("pat3:", pat3.pattern, "->", pat3.findall(fa))
# also check header names
print("has File Attachments col:", "File Attachments" in rows[0].keys())
