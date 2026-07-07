# ACT-Software-Project

## About
Automates the manual process of reading "Weekly Cards" emails sent to the department
manager in Outlook, and saving the relevant data into a CSV/database.

## How it works
- Each card sends two emails: `Pending` (ignored) and `Approved` (the one we need).
- The script filters out irrelevant emails and picks out only the approved cards.
- It extracts key info from the email and saves it to a CSV/database automatically.

## Status
🚧 Work in progress — internship project.

## Tech
Python + Outlook (offline, via local Outlook client)