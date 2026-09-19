"""Additive document sandbox for native CORS collection contexts.

Target-created callbacks cannot reliably prevent the first popup request. A
header-delivered sandbox is enforced before a document runs, without allowing
auxiliary browsing contexts. Existing publisher policies remain separate and
are all still enforced. Bodies, Cookie headers, CORS responses and TLS remain
browser-managed; this helper never grants a request or changes authentication.
"""
from __future__ import annotations

# Preserve scripts, the original origin and same-tab normal forms. Do not grant
# popups, popup escape, downloads, top navigation or other unsupported surfaces.
DOCUMENT_SANDBOX = 'sandbox allow-scripts allow-same-origin allow-forms'


def document_response_params(event: dict, *, enabled: bool) -> dict:
    """Build continueResponse parameters, with one extra restriction if needed.

    Called only after the normal status, redirect, size and accounting checks.
    Keep header order/case/duplicates, including every original CSP/Set-Cookie.
    Appending an independent enforcing CSP cannot relax a publisher policy.
    """
    params = {'requestId': event['requestId']}
    status = event.get('responseStatusCode', 0)
    if (not enabled or event.get('resourceType') != 'Document'
            or 'responseErrorReason' in event or status < 200 or 300 <= status < 400):
        return params
    params.update(responseCode=status,
                  responsePhrase=event.get('responseStatusText', ''),
                  responseHeaders=[dict(h) for h in event.get('responseHeaders', [])] + [
                      {'name': 'Content-Security-Policy', 'value': DOCUMENT_SANDBOX}])
    return params
