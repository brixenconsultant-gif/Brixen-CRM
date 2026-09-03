=== Brixen Consultants CRM & WooCommerce Sync ===
Contributors: Brixen Consultants Engineering Team
Tags: woocommerce, crm, sync, webhooks, sso, client portal
Requires at least: 5.8
Tested up to: 6.7
Stable tag: 1.6.0
License: Proprietary

Official integration plugin for Brixen Consultants connecting WordPress User Registration and WooCommerce Checkout to the Brixen CRM & Client Portal.

== Description ==

This plugin provides automated real-time synchronization between the official Brixen Consultants WordPress website (https://brixenconsultants.com/) and the Brixen CRM / Client Portal:

1. **WordPress User Registration Sync**: Automatically creates/updates CRM Client records when a new WordPress user registers or updates their profile (`user.created`, `user.updated`).
2. **WooCommerce Checkout & Order Sync**: Automatically maps completed WooCommerce checkouts to CRM orders linked via `wordpress_user_id` and customer email (`order.created`, `order.updated`, `payment.completed`).
3. **HMAC-SHA256 Security**: All outgoing integration webhooks are signed using HMAC-SHA256 with header `X-Brixen-Signature` for cryptographic verification.
4. **Idempotency & Retry Safety**: Unique event IDs (`evt_...`) prevent duplicate processing across network retries.
5. **SSO Bridge Shortcode**: Provides `[brixen_client_portal_button]` to render a 1-click single sign-on redirect into the Brixen Client Portal.
6. **Messages & Files**: Shows CRM-uploaded client documents on WooCommerce **Messages & Files** (`/client-panel/customer-messages/`) and via shortcode `[brixen_messages_files]`.

== Installation ==

1. Upload the `brixen-crm-sync` folder to `/wp-content/plugins/` directory.
2. Activate the plugin through the 'Plugins' menu in WordPress.
3. Navigate to 'Brixen CRM' in the WordPress Admin sidebar.
4. Enter your CRM Base URL (`https://portal.brixenconsultants.com`) and shared Webhook Secret. The client dashboard is the same URL.
5. Save settings.
6. Click **Sync existing orders to CRM** so past company-registration checkouts appear on Company Registered.
7. After updating to 1.2.0, visit **Settings → Permalinks** and click Save once (refreshes the Messages & Files endpoint).

== Webhook Event Types ==

* `user.created`: Fired when a new user registers on WordPress.
* `user.updated`: Fired when a user updates their profile details.
* `order.created`: Fired when a new WooCommerce checkout is completed.
* `order.updated`: Fired when a WooCommerce order status changes.
* `payment.completed`: Fired when payment is confirmed for an order.

== Changelog ==

= 1.6.0 =
* Production release alignment for client portal document delivery, signed SSO redirect flow, and WooCommerce checkout sync.

= 1.5.0 =
* After login and on `/client-panel/`, customers are redirected via signed SSO to the custom Brixen client portal at portal.brixenconsultants.com (not the WooCommerce account dashboard).
* Adds `brixen_crm_portal_sso_url()` helper for theme/header menus.
* SSO shortcode opens the portal in the same tab.

= 1.2.5 =
* Publish the square website favicon at /apple-touch-icon.png and /favicon.ico so mail apps can show the Brixen icon.

= 1.2.4 =
* Validate UK phone numbers and always format them with +44 country code.
* Ignore uploaded document filenames that were incorrectly detected as phone numbers.

= 1.2.3 =
* Send full company formation checkout meta (`_cfs_*`, DOB, director details) and order notes to the CRM.
* Add signed CRM pull endpoint so the portal can refresh missing DOB and phone from the website order.
* Include date of birth and extra phone fields from WordPress user profiles.

= 1.2.2 =
* Sync all WooCommerce products into the CRM Services Catalog and Orders Manager product filter.
* Add "Sync products to CRM" button; products also sync when saved in WooCommerce.

= 1.2.1 =
* Sync customer checkout file uploads into CRM Orders Manager → Documents.
* Include attachment URLs / media IDs from order and line-item meta in order webhooks.

= 1.2.0 =
* Show CRM posted documents in Client Panel → Messages & Files.
* Add `[brixen_messages_files]` shortcode for the same document list.
* Secure document list/download via signed CRM API (no separate CRM login required).

= 1.1.1 =
* Sync existing WooCommerce orders automatically when the plugin is activated.

= 1.1.0 =
* Send the checkout company name with every order webhook.
* Keep the full product name on order updates (no more blank "Corporate Formation Service" overwrites).
* Add a WordPress admin button to sync existing WooCommerce formation orders into CRM Company Registered cards.
