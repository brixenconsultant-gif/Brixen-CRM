<?php
$replacements = array(
    'digital-package-company-registration' => 'digital-package-name-search',
    'professional-package-company-registration' => 'professional-package-name-search',
    'all-inclusive-package-company-registration' => 'all-inclusive-name-search',
);
$ids = array(13042, 13077, 13514);
foreach ($ids as $id) {
    $raw = get_post_meta($id, '_elementor_data', true);
    if (!is_string($raw) || $raw === '') {
        echo "skip $id empty\n";
        continue;
    }
    $updated = str_replace(array_keys($replacements), array_values($replacements), $raw);
    if ($updated === $raw) {
        echo "skip $id no change\n";
        continue;
    }
    update_post_meta($id, '_elementor_data_backup_before_namesearch_links', $raw);
    update_post_meta($id, '_elementor_data', wp_slash($updated));
    echo "OK $id\n";
}
if (class_exists('\Elementor\Plugin')) {
    \Elementor\Plugin::$instance->files_manager->clear_cache();
    echo "cache cleared\n";
}
