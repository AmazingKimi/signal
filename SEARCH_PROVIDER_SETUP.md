# SIGNAL 0.8.4C — Search Provider Setup

## Supported providers

- Tavily
- Brave Search API

SIGNAL does not use DuckDuckGo/ddgs or target-site fetching as a production fallback.

## Configure in the application

1. Start SIGNAL and open **Settings → Search Service**.
2. Select **Tavily** or **Brave Search**.
3. Enter that provider's API key.
4. Click **Save & Test**.
5. Wait for **Connection successful / 连接成功**.

Save & Test performs one minimal request to the selected provider's official API. The saved provider becomes active immediately and remains active after restart. Tavily and Brave keys are stored separately. Switching the selected provider never triggers automatic failover.

## Environment bootstrap

Fresh `.env` files use independent variables:

```env
SEARCH_PROVIDER=
TAVILY_API_KEY=
BRAVE_API_KEY=
```

The Settings page is the normal configuration path. Environment values remain supported for bootstrap. A legacy `SEARCH_PROVIDER=duckduckgo` line is changed to an empty provider by `start.command`; all unrelated DeepSeek, SMTP, and user configuration lines remain untouched.

## Security

- API keys are stored only in the local runtime configuration.
- API responses expose only provider state and whether a key is present.
- Keys are never returned to the browser, HTML, console, or ordinary logs.
- Developer error details contain a safe HTTP status or exception class, never the complete key.

## Run behavior

Radar checks provider configuration before selecting sources, generating queries, or creating History. If no official provider is configured, the UI asks the user to configure Search Service and no `0/N failed` run is recorded.
