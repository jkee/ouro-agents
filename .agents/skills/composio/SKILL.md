---
name: "Composio Integration"
description: "Connect and use 250+ external apps (Gmail, Slack, GitHub, Notion, etc.) via Composio OAuth"
version: "1.0.0"
tags: ["integrations", "oauth", "external-apps", "composio"]
---

# Composio Integration

Connect to and interact with 250+ external apps through Composio's OAuth layer. The agent never sees user credentials — Composio handles all token management.

## Tools

Enable these tools with `enable_tools`:
- `composio_list_connections` — see what apps are connected
- `composio_get_oauth_url` — generate OAuth link for a new app
- `composio_run_action` — execute actions on connected apps
- `composio_request_app` — request project creator to enable a new app

## Quick Start

1. **Check connections**: `composio_list_connections()` — see what's already authorized
2. **Connect an app**: `composio_get_oauth_url(app="GMAIL")` — get OAuth URL, send to user
3. **Use an app**: `composio_run_action(action="GMAIL_FETCH_EMAILS", params={"max_results": 5})`

## Common Actions

### Gmail
- `GMAIL_FETCH_EMAILS` — params: `{"max_results": 10}`
- `GMAIL_SEND_EMAIL` — params: `{"to": "...", "subject": "...", "body": "..."}`

### GitHub
- `GITHUB_LIST_ISSUES` — params: `{"repo": "owner/repo"}`
- `GITHUB_CREATE_AN_ISSUE` — params: `{"repo": "owner/repo", "title": "...", "body": "..."}`

### Slack
- `SLACK_SEND_MESSAGE` — params: `{"channel": "#general", "text": "..."}`

### Notion
- `NOTION_CREATE_PAGE` — params: `{"parent_id": "...", "title": "...", "content": "..."}`

## Connecting New Apps

If an app is not connected:
1. Call `composio_get_oauth_url(app="APP_NAME")` to get the authorization link
2. Send the link to the user to complete OAuth
3. Once authorized, the connection persists via refresh tokens

## Requesting New Apps

Some apps require the Composio project owner to enable them first. If you get an error that an app is not available:

1. Call `composio_request_app(app="APP_NAME", reason="why it's needed")`
2. This creates a GitHub issue tagged `composio-app-request`
3. Tell the user to ask the project creator (Viktor Tarnavskii) to enable it
4. Once enabled, proceed with `composio_get_oauth_url`

## Discovering Apps and Actions

To find available apps and their actions, use `composio_run_action` or check https://composio.dev/apps for the full catalog. Common app names: GMAIL, GITHUB, SLACK, NOTION, LINEAR, JIRA, HUBSPOT, TRELLO, ASANA, DISCORD, GOOGLE_CALENDAR, GOOGLE_DRIVE, DROPBOX, TWITTER.
