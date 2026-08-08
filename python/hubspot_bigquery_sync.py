"""
Pulls HubSpot contacts and deals into BigQuery on a schedule, and pushes
enriched campaign attribution fields back to HubSpot as custom contact
properties. The sync is one-directional per hop: HubSpot to BigQuery for raw
data, BigQuery to HubSpot for the enriched attribution fields only.
"""

import logging

import requests
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hubspot_bigquery_sync")

HUBSPOT_API_BASE = "https://api.hubapi.com"
HUBSPOT_TOKEN = "REPLACE_WITH_PRIVATE_APP_TOKEN"
HEADERS = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}


def fetch_hubspot_contacts(limit: int = 100) -> list[dict]:
    """Pulls contacts from HubSpot, paginating through the full result set."""
    contacts = []
    after = None

    while True:
        params = {
            "limit": limit,
            "properties": "email,firstname,lastname,createdate,hs_analytics_source",
        }
        if after:
            params["after"] = after

        response = requests.get(f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts", headers=HEADERS, params=params)
        response.raise_for_status()
        payload = response.json()

        contacts.extend(payload.get("results", []))

        paging = payload.get("paging", {}).get("next")
        if not paging:
            break
        after = paging["after"]

    return contacts


def load_contacts_to_bigquery(contacts: list[dict], bq_client: bigquery.Client) -> None:
    rows = [
        {
            "contact_id": c["id"],
            "email": c["properties"].get("email"),
            "first_name": c["properties"].get("firstname"),
            "last_name": c["properties"].get("lastname"),
            "created_at": c["properties"].get("createdate"),
            "hubspot_source": c["properties"].get("hs_analytics_source"),
        }
        for c in contacts
    ]

    table_id = "project.raw.hubspot_contacts"
    errors = bq_client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"Failed to load HubSpot contacts: {errors}")
    logger.info(f"Loaded {len(rows)} contacts into {table_id}")


def push_attribution_to_hubspot(bq_client: bigquery.Client) -> None:
    """Reads enriched attribution from BigQuery and pushes it back as HubSpot properties."""
    query = """
        select contact_id, campaign_source, first_touch_channel, attribution_confidence
        from project.marts.contact_attribution
        where updated_at >= timestamp_sub(current_timestamp(), interval 1 day)
    """
    rows = list(bq_client.query(query).result())

    for row in rows:
        payload = {
            "properties": {
                "campaign_source": row.campaign_source,
                "first_touch_channel": row.first_touch_channel,
                "attribution_confidence": row.attribution_confidence,
            }
        }
        response = requests.patch(
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{row.contact_id}",
            headers=HEADERS,
            json=payload,
        )
        if response.status_code != 200:
            logger.warning(f"Failed to update contact {row.contact_id}: {response.text}")

    logger.info(f"Pushed attribution updates for {len(rows)} contacts back to HubSpot")


def main() -> None:
    bq_client = bigquery.Client()

    contacts = fetch_hubspot_contacts()
    logger.info(f"Fetched {len(contacts)} contacts from HubSpot")
    load_contacts_to_bigquery(contacts, bq_client)

    # Enrichment (sql/enrich_contacts_with_campaign_source.sql) runs separately
    # as a scheduled query, then this step pushes the results back.
    push_attribution_to_hubspot(bq_client)


if __name__ == "__main__":
    main()
