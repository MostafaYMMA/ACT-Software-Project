import tkinter as tk
from tkinter import ttk
import re
import win32com.client


def scan_inbox():
    status.config(text="Scanning Inbox...")
    root.update()

    results.delete(1.0, tk.END)

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)

    total = 0
    read = 0
    unread = 0
    approved_count = 0
    matching_count = 0

    approved_pattern = re.compile(r"\bapproved\b", re.IGNORECASE)

    subject_pattern = re.compile(
        r"\btime(?:\s+|-)?card\b|^\s*FW:\s*FYI:?",
        re.IGNORECASE
    )

    for item in inbox.Items:
        try:
            if item.Class != 43:
                continue

            total += 1

            if item.UnRead:
                unread += 1
            else:
                read += 1

            subject = item.Subject or ""

            if approved_pattern.search(subject):
                approved_count += 1

            if approved_pattern.search(subject) and subject_pattern.search(subject):
                matching_count += 1
                results.insert(tk.END, subject + "\n")

        except:
            pass

    lbl_total.config(text=f"{total:,}")
    lbl_read.config(text=f"{read:,}")
    lbl_unread.config(text=f"{unread:,}")
    lbl_approved.config(text=f"{approved_count:,}")
    lbl_matching.config(text=f"{matching_count:,}")

    status.config(text="Finished")


# ---------------- GUI ----------------

root = tk.Tk()
root.title("Outlook Mail Counter")
root.geometry("850x650")
root.resizable(False, False)

title = tk.Label(
    root,
    text="Outlook Inbox Statistics",
    font=("Segoe UI", 18, "bold")
)
title.pack(pady=10)

frame = ttk.Frame(root)
frame.pack(pady=10)

labels = [
    ("Total Emails", 0),
    ("Read Emails", 1),
    ("Unread Emails", 2),
    ("Approved Subjects", 3),
    ("Approved + Time Card/FW:FYI", 4)
]

value_labels = []

for text, row in labels:
    ttk.Label(frame, text=text + ":", font=("Segoe UI", 11, "bold")).grid(
        row=row, column=0, sticky="w", padx=10, pady=5
    )

    lbl = ttk.Label(frame, text="0", font=("Segoe UI", 11))
    lbl.grid(row=row, column=1, sticky="w", padx=10)

    value_labels.append(lbl)

lbl_total = value_labels[0]
lbl_read = value_labels[1]
lbl_unread = value_labels[2]
lbl_approved = value_labels[3]
lbl_matching = value_labels[4]

ttk.Button(
    root,
    text="Scan Inbox",
    command=scan_inbox
).pack(pady=10)

status = ttk.Label(root, text="Ready")
status.pack()

ttk.Label(
    root,
    text="Matching Subjects",
    font=("Segoe UI", 12, "bold")
).pack(pady=(10, 0))

frame2 = ttk.Frame(root)
frame2.pack(fill="both", expand=True, padx=10, pady=10)

scroll = ttk.Scrollbar(frame2)
scroll.pack(side="right", fill="y")

results = tk.Text(
    frame2,
    height=20,
    yscrollcommand=scroll.set,
    font=("Consolas", 10)
)

results.pack(fill="both", expand=True)

scroll.config(command=results.yview)

root.mainloop()
