require("dotenv").config();

const fs = require("fs");
const path = require("path");

const API_KEY = process.env.APOLLO_API_KEY;
const PER_PAGE = 100;
const OUTPUT_FILE = path.join(__dirname, "apollo-contacts.csv");

if (!API_KEY) {
  console.error("Missing APOLLO_API_KEY in .env");
  process.exit(1);
}

function csvEscape(value) {
  if (value === null || value === undefined) {
    return "";
  }

  const stringValue = String(value).replace(/\r?\n/g, " ");

  if (
    stringValue.includes(",") ||
    stringValue.includes('"') ||
    stringValue.includes("\n")
  ) {
    return `"${stringValue.replace(/"/g, '""')}"`;
  }

  return stringValue;
}

async function fetchContactsPage(page) {
  const response = await fetch(
    `https://api.apollo.io/api/v1/contacts/search?page=${page}&per_page=${PER_PAGE}`,
    {
      method: "POST",
      headers: {
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        accept: "application/json",
        "x-api-key": API_KEY
      }
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `Apollo contacts search failed for page ${page}: ${response.status} ${errorText}`
    );
  }

  return response.json();
}

function contactToRow(contact) {
  return [
    contact.id,
    contact.person_id,
    contact.name,
    contact.first_name,
    contact.last_name,
    contact.title,
    contact.organization_name,
    contact.email,
    contact.linkedin_url,
    contact.formatted_address,
    contact.city,
    contact.state,
    contact.country,
    contact.created_at,
    contact.updated_at
  ];
}

async function main() {
  const firstPage = await fetchContactsPage(1);
  const totalEntries = firstPage.pagination?.total_entries ?? 0;
  const totalPages = Math.max(
    1,
    Math.ceil(totalEntries / (firstPage.pagination?.per_page || PER_PAGE))
  );
  const allContacts = [...(firstPage.contacts || [])];

  console.log(
    `Fetched page 1/${totalPages} (${allContacts.length}/${totalEntries} contacts)`
  );

  for (let page = 2; page <= totalPages; page += 1) {
    const result = await fetchContactsPage(page);
    const contacts = result.contacts || [];
    allContacts.push(...contacts);

    console.log(
      `Fetched page ${page}/${totalPages} (${allContacts.length}/${totalEntries} contacts)`
    );
  }

  const header = [
    "contact_id",
    "person_id",
    "name",
    "first_name",
    "last_name",
    "title",
    "organization_name",
    "email",
    "linkedin_url",
    "formatted_address",
    "city",
    "state",
    "country",
    "created_at",
    "updated_at"
  ];

  const lines = [header, ...allContacts.map(contactToRow)].map((row) =>
    row.map(csvEscape).join(",")
  );

  fs.writeFileSync(OUTPUT_FILE, `${lines.join("\n")}\n`, "utf8");

  console.log(`Exported ${allContacts.length} contacts to ${OUTPUT_FILE}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
