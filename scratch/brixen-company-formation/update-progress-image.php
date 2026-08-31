<?php
$url = wp_get_attachment_url(14005);
foreach (array(12897, 12952, 12989) as $id) {
    $data = json_decode(get_post_meta($id, '_elementor_data', true), true);
    $walk = function (&$els) use (&$walk, $url) {
        foreach ($els as &$el) {
            if (($el['id'] ?? '') === 'c7ef8da') {
                $el['settings']['image']['id'] = 14005;
                $el['settings']['image']['url'] = $url;
            }
            if (!empty($el['elements'])) {
                $walk($el['elements']);
            }
        }
        unset($el);
    };
    $walk($data);
    update_post_meta($id, '_elementor_data', wp_slash(wp_json_encode($data)));
    echo $id . " progress=" . $url . "\n";
}
if (class_exists('\Elementor\Plugin')) {
    \Elementor\Plugin::$instance->files_manager->clear_cache();
    echo "cache cleared\n";
}
