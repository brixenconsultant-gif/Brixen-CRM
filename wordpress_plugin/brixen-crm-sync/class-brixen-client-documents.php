<?php
/**
 * Show CRM posted documents inside WooCommerce Messages & Files.
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit;
}

class Brixen_CRM_Client_Documents {

    public static function init() {
        add_action('init', array(__CLASS__, 'register_endpoint'));
        add_filter('woocommerce_account_menu_items', array(__CLASS__, 'ensure_menu_item'), 40);
        add_action('woocommerce_account_customer-messages_endpoint', array(__CLASS__, 'render_account_documents'), 30);
        add_shortcode('brixen_messages_files', array(__CLASS__, 'render_shortcode'));
    }

    public static function register_endpoint() {
        add_rewrite_endpoint('customer-messages', EP_ROOT | EP_PAGES);
    }

    public static function ensure_menu_item($items) {
        if (!is_array($items)) {
            return $items;
        }
        if (isset($items['customer-messages'])) {
            $items['customer-messages'] = __('Messages & Files', 'brixen-crm-sync');
            return $items;
        }
        $new_items = array();
        foreach ($items as $key => $label) {
            $new_items[$key] = $label;
            if ($key === 'orders') {
                $new_items['customer-messages'] = __('Messages & Files', 'brixen-crm-sync');
            }
        }
        if (!isset($new_items['customer-messages'])) {
            $new_items['customer-messages'] = __('Messages & Files', 'brixen-crm-sync');
        }
        return $new_items;
    }

    public static function signed_query_args($user = null) {
        $user = $user ?: wp_get_current_user();
        if (!$user || !$user->ID) {
            return null;
        }
        $secret = Brixen_CRM_Webhook_Sender::get_webhook_secret();
        if (!$secret) {
            return null;
        }
        $user_id = (string) $user->ID;
        $email = (string) $user->user_email;
        $timestamp = (string) time();
        $signature = hash_hmac('sha256', $user_id . '|' . $email . '|' . $timestamp, $secret);
        return array(
            'wordpress_user_id' => $user_id,
            'email'             => $email,
            'timestamp'         => $timestamp,
            'signature'         => $signature,
        );
    }

    public static function fetch_documents($user = null) {
        $crm_url = rtrim((string) Brixen_CRM_Webhook_Sender::get_crm_url(), '/');
        $args = self::signed_query_args($user);
        if (!$crm_url || !$args) {
            return array(
                'ok' => false,
                'message' => __('CRM connection is not configured yet.', 'brixen-crm-sync'),
                'documents' => array(),
            );
        }
        $url = add_query_arg($args, $crm_url . '/api/v1/wordpress/documents');
        $response = wp_remote_get($url, array(
            'timeout'   => 20,
            'sslverify' => true,
        ));
        if (is_wp_error($response)) {
            return array(
                'ok' => false,
                'message' => $response->get_error_message(),
                'documents' => array(),
            );
        }
        $code = (int) wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code < 200 || $code >= 300 || !is_array($body) || ($body['status'] ?? '') !== 'success') {
            return array(
                'ok' => false,
                'message' => is_array($body) ? ($body['message'] ?? __('Unable to load documents.', 'brixen-crm-sync')) : __('Unable to load documents.', 'brixen-crm-sync'),
                'documents' => array(),
            );
        }
        return array(
            'ok' => true,
            'message' => '',
            'documents' => is_array($body['documents'] ?? null) ? $body['documents'] : array(),
        );
    }

    public static function render_shortcode() {
        return self::render_documents_html();
    }

    public static function render_account_documents() {
        echo self::render_documents_html();
    }

    public static function render_documents_html() {
        if (!is_user_logged_in()) {
            return '<div class="cmp-no-messages">' . esc_html__('Please log in to view your messages and files.', 'brixen-crm-sync') . '</div>';
        }

        $result = self::fetch_documents();
        $documents = $result['documents'];

        ob_start();
        ?>
        <div class="cmp-customer-section brixen-crm-files">
            <h3><?php echo esc_html__('Files from Brixen', 'brixen-crm-sync'); ?></h3>
            <p style="color:#666; margin-bottom:16px;">
                <?php echo esc_html__('Documents uploaded by our team (for example certificates received by post) appear here.', 'brixen-crm-sync'); ?>
            </p>
            <div class="cmp-messages-container">
                <?php if (!$result['ok']) : ?>
                    <div class="cmp-no-messages"><?php echo esc_html($result['message']); ?></div>
                <?php elseif (empty($documents)) : ?>
                    <div class="cmp-no-messages"><?php echo esc_html__('No files have been shared with your account yet.', 'brixen-crm-sync'); ?></div>
                <?php else : ?>
                    <?php foreach ($documents as $doc) : ?>
                        <?php
                        $name = (string) ($doc['name'] ?? 'Document');
                        $category = (string) ($doc['category'] ?? 'Posted Documents');
                        $company = (string) ($doc['company_name'] ?? '');
                        $created = (string) ($doc['created_at'] ?? '');
                        $message = (string) ($doc['message'] ?? '');
                        $download = (string) ($doc['download_url'] ?? '');
                        $date_label = $created ? date_i18n(get_option('date_format') . ' ' . get_option('time_format'), strtotime($created)) : '';
                        ?>
                        <div class="cmp-message-box">
                            <div class="cmp-message-meta">
                                <span class="cmp-message-service"><?php echo esc_html($category); ?></span>
                                <?php if ($date_label) : ?>
                                    <span class="cmp-message-date"><?php echo esc_html($date_label); ?></span>
                                <?php endif; ?>
                            </div>
                            <div class="cmp-message-content">
                                <strong><?php echo esc_html($name); ?></strong>
                                <?php if ($company) : ?>
                                    <div style="color:#666; margin-top:6px;"><?php echo esc_html($company); ?></div>
                                <?php endif; ?>
                                <?php if ($message) : ?>
                                    <div style="margin-top:10px;"><?php echo esc_html($message); ?></div>
                                <?php endif; ?>
                            </div>
                            <?php if ($download) : ?>
                                <div class="cmp-attachments">
                                    <div class="cmp-attachment">
                                        <a href="<?php echo esc_url($download); ?>" target="_blank" rel="noopener">
                                            <span class="cmp-attachment-icon">⬇</span>
                                            <?php echo esc_html__('Download file', 'brixen-crm-sync'); ?>
                                        </a>
                                    </div>
                                </div>
                            <?php endif; ?>
                        </div>
                    <?php endforeach; ?>
                <?php endif; ?>
            </div>
        </div>
        <?php
        return ob_get_clean();
    }
}
