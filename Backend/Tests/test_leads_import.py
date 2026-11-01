from routes.flintdesk import leads


def test_manual_lead_import_endpoint_is_disabled():
    """Leads now arrive from the Google Sheet (services/lead_sync)."""
    paths = {route.path for route in leads.router.routes}
    assert "/api/flintdesk/leads/import" not in paths
    assert not hasattr(leads, "import_leads")
