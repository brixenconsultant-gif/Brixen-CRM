=== Brixen Consultants CRM & WooCommerce Sync ===
Contributors: Brixen Consultants Engineering Team
Tags: woocommerce, crm, sync, webhooks, sso, client portal
Requires at least: 5.8
Tested up to: 6.7
Stable tag: 1.0.0
License: Proprietary

Official integration plugin for Brixen Consultants connecting WordPress User Registration and WooCommerce Checkout to the Brixen CRM & Client Portal.

== Description ==

This plugin provides automated real-time synchronization between the official Brixen Consultants WordPress website (https://brixenconsultants.com/) and the Brixen CRM / Client Portal:

1. **WordPress User Registration Sync**: Automatically creates/updates CRM Client records when a new WordPress user registers or updates their profile (`user.created`, `user.updated`).
2. **WooCommerce Checkout & Order Sync**: Automatically maps completed WooCommerce checkouts to CRM orders linked via `wordpress_user_id` and customer email (`order.created`, `order.updated`, `payment.completed`).
3. **HMAC-SHA256 Security**: All outgoing integration webhooks are signed using HMAC-SHA256 with header `X-Brixen-Signature` for cryptographic verification.
4. **Idempotency & Retry Safety**: Unique event IDs (`evt_...`) prevent duplicate processing across network retries.
5. **SSO Bridge Shortcode**: Provides `[brixen_client_portal_button]` to render a 1-click single sign-on redirect into the Brixen Client Portal.

== Installation ==

1. Upload the `brixen-crm-sync` folder to `/wp-content/plugins/` directory.
2. Activate the plugin through the 'Plugins' menu in WordPress.
3. Navigate to 'Brixen CRM' in the WordPress Admin sidebar.
4. Enter your CRM Base URL (e.g., `http://127.0.0.1:5050` in dev or `https://portal.brixenconsultants.com` in production) and shared Webhook Secret.
5. Save settings.

== Webhook Event Types ==

* `user.created`: Fired when a new user registers on WordPress.
* `user.updated`: Fired when a user updates their profile details.
* `order.created`: Fired when a new WooCommerce checkout is completed.
* `order.updated`: Fired when a WooCommerce order status changes.
* `payment.completed`: Fired when payment is confirmed for an order.
