-- Joins synced HubSpot contacts against GA4 session data to determine each
-- contact's first touch campaign and source. Output feeds directly into the
-- push-back step in hubspot_bigquery_sync.py.

with hubspot_contacts as (
  select
    contact_id,
    email,
    created_at,
    hubspot_source
  from `project.raw.hubspot_contacts`
),

ga4_first_touch as (
  select
    (select value.string_value from unnest(event_params) where key = 'email') as email,
    (select value.string_value from unnest(event_params) where key = 'campaign') as campaign,
    (select value.string_value from unnest(event_params) where key = 'source') as source,
    (select value.string_value from unnest(event_params) where key = 'medium') as medium,
    min(timestamp_micros(event_timestamp)) as first_session_ts
  from `project.analytics_XXXXXXXX.events_*`
  where event_name = 'session_start'
  group by email, campaign, source, medium
),

enriched as (
  select
    h.contact_id,
    h.email,
    h.created_at,
    g.campaign as ga4_campaign,
    g.source as ga4_source,
    g.medium as ga4_medium,
    h.hubspot_source,
    case
      when g.campaign is not null then g.source || ' / ' || g.campaign
      when h.hubspot_source is not null then h.hubspot_source
      else null
    end as campaign_source,
    coalesce(g.medium, 'unknown') as first_touch_channel,
    case
      when g.campaign is not null then 'high'
      when h.hubspot_source is not null then 'medium'
      else 'low'
    end as attribution_confidence
  from hubspot_contacts h
  left join ga4_first_touch g
    on lower(h.email) = lower(g.email)
)

select
  contact_id,
  campaign_source,
  first_touch_channel,
  attribution_confidence,
  current_timestamp() as updated_at
from enriched;
