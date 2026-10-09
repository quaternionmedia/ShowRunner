"""Playwright tests for what the page tests can't see: a real browser on a live server."""

from playwright.sync_api import expect


def test_dashboard_renders(page, live_server):
    page.goto(live_server)
    expect(page.get_by_text('Show Control Dashboard')).to_be_visible()


def test_header_selects_the_seeded_show(page, live_server):
    page.goto(live_server)
    expect(page.locator('header').get_by_text('E2E Show')).to_be_visible()


def test_menu_navigates_to_scripts(page, live_server):
    page.goto(live_server)
    page.locator('header button').first.click()
    page.get_by_text('Scripts', exact=True).click()
    expect(page).to_have_url(f'{live_server}/script')
    for layer in ('Lights', 'Sound', 'Video', 'Audio', 'Stage'):
        expect(page.get_by_role('button', name=layer)).to_be_visible()
