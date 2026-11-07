<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');

function reply(int $status, string $message): void {
    http_response_code($status);
    echo json_encode($status === 200 ? ['ok' => true] : ['error' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

function clean_text($value, int $max): string {
    if (!is_string($value)) return '';
    $value = preg_replace('/[\x00-\x1F\x7F]/u', ' ', $value);
    if ($value === null) return '';
    $value = preg_replace('/\s+/u', ' ', trim($value));
    if ($value === null) return '';
    if (function_exists('mb_substr')) return mb_substr($value, 0, $max + 1, 'UTF-8');
    $chars = preg_split('//u', $value, -1, PREG_SPLIT_NO_EMPTY);
    return $chars === false ? '' : implode('', array_slice($chars, 0, $max + 1));
}

function length_ok(string $value, int $min, int $max): bool {
    if (function_exists('mb_strlen')) $length = mb_strlen($value, 'UTF-8');
    else {
        $chars = preg_split('//u', $value, -1, PREG_SPLIT_NO_EMPTY);
        $length = $chars === false ? 0 : count($chars);
    }
    return $length >= $min && $length <= $max;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Allow: POST');
    reply(405, 'Method not allowed');
}

if (strpos(strtolower($_SERVER['CONTENT_TYPE'] ?? ''), 'application/json') !== 0) {
    reply(415, 'JSON required');
}

$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if ($origin !== '' && !in_array($origin, ['https://jaxgo.ru', 'https://www.jaxgo.ru'], true)) {
    reply(403, 'Origin not allowed');
}

if ((int) ($_SERVER['CONTENT_LENGTH'] ?? 0) > 8192) reply(413, 'Payload too large');
$raw = file_get_contents('php://input', false, null, 0, 8193);
if ($raw === false || strlen($raw) > 8192) reply(413, 'Payload too large');
$body = json_decode($raw, true);
if (!is_array($body)) reply(400, 'Invalid JSON');

$name = clean_text($body['name'] ?? null, 100);
$contact = clean_text($body['contact'] ?? null, 150);
$message = clean_text($body['message'] ?? null, 3000);
$website = clean_text($body['website'] ?? null, 200);
$page = clean_text($body['page'] ?? null, 200);
if (!length_ok($name, 2, 100) || !length_ok($contact, 3, 150) ||
    !length_ok($message, 10, 3000) || ($body['consent'] ?? null) !== true) {
    reply(400, 'Check the form fields');
}
if ($website !== '') reply(200, ''); // Honeypot: do not send bot traffic.

$rateFile = sys_get_temp_dir() . '/jaxgo-' . sha1(__DIR__) . '-rate.json';
$lock = fopen($rateFile, 'c+');
if ($lock === false || !flock($lock, LOCK_EX)) reply(503, 'Delivery unavailable');
$saved = stream_get_contents($lock);
$counts = json_decode($saved ?: '{}', true);
if (!is_array($counts)) $counts = [];
$now = time();
foreach ($counts as $key => $entry) {
    if (!is_array($entry) || ($entry['reset'] ?? 0) <= $now) unset($counts[$key]);
}
$ipKey = hash('sha256', $_SERVER['REMOTE_ADDR'] ?? 'unknown');
$entry = $counts[$ipKey] ?? ['count' => 0, 'reset' => $now + 600];
if ($entry['count'] >= 5) {
    flock($lock, LOCK_UN);
    fclose($lock);
    header('Retry-After: ' . max(1, $entry['reset'] - $now));
    reply(429, 'Too many requests');
}
$entry['count']++;
$counts[$ipKey] = $entry;
rewind($lock);
ftruncate($lock, 0);
fwrite($lock, json_encode($counts));
fflush($lock);
flock($lock, LOCK_UN);
fclose($lock);

$dir = __DIR__;
for ($i = 0; $i < 5 && basename($dir) !== 'public_html'; $i++) $dir = dirname($dir);
if (basename($dir) !== 'public_html') reply(503, 'Delivery unavailable');
$secretPath = dirname($dir) . '/jaxgo-secrets.php';
if (!is_file($secretPath)) reply(503, 'Delivery unavailable');
$config = require $secretPath;
if (!is_array($config)) reply(503, 'Delivery unavailable');
$channels = array_map('trim', explode(',', strtolower((string) ($config['lead_channels'] ?? 'telegram'))));

$lines = [
    'Новая заявка с jaxgo.ru',
    'Получена: ' . gmdate('c'),
    'Согласие на обработку: подтверждено, редакция 2026-09-24',
    'Имя: ' . $name,
    'Контакт: ' . $contact,
    'Задача: ' . $message,
];
if ($page !== '') $lines[] = 'Страница: ' . $page;
$utm = $body['utm'] ?? [];
if (is_array($utm)) {
    foreach (['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'] as $key) {
        $value = clean_text($utm[$key] ?? null, 120);
        if ($value !== '') $lines[] = $key . ': ' . $value;
    }
}

if (!function_exists('curl_init')) reply(503, 'Delivery unavailable');
$text = implode("\n", $lines);
$attempted = false;
$delivered = false;

if (in_array('telegram', $channels, true)) {
    $token = $config['telegram_bot_token'] ?? '';
    $chatId = $config['telegram_chat_id'] ?? '';
    if (is_string($token) && $token !== '' && is_string($chatId) && $chatId !== '') {
        $attempted = true;
        $curl = curl_init('https://api.telegram.org/bot' . $token . '/sendMessage');
        curl_setopt_array($curl, [
            CURLOPT_POST => true,
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_POSTFIELDS => json_encode(['chat_id' => $chatId, 'text' => $text, 'disable_web_page_preview' => true], JSON_UNESCAPED_UNICODE),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => 10,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ]);
        $result = curl_exec($curl);
        $status = curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        $decoded = is_string($result) ? json_decode($result, true) : null;
        $delivered = $status === 200 && is_array($decoded) && ($decoded['ok'] ?? false) === true;
    }
}

if (in_array('max', $channels, true)) {
    $maxToken = $config['max_bot_token'] ?? '';
    $maxChatId = $config['max_chat_id'] ?? '';
    if (is_string($maxToken) && $maxToken !== '' && is_string($maxChatId) && preg_match('/^-?\d+$/', $maxChatId)) {
        $attempted = true;
        if (function_exists('mb_substr')) $maxText = mb_substr($text, 0, 4000, 'UTF-8');
        else {
            $chars = preg_split('//u', $text, -1, PREG_SPLIT_NO_EMPTY);
            $maxText = $chars === false ? '' : implode('', array_slice($chars, 0, 4000));
        }
        $curl = curl_init('https://platform-api2.max.ru/messages?chat_id=' . rawurlencode($maxChatId));
        $options = [
            CURLOPT_POST => true,
            CURLOPT_HTTPHEADER => ['Authorization: ' . $maxToken, 'Content-Type: application/json'],
            CURLOPT_POSTFIELDS => json_encode(['text' => $maxText, 'disable_link_preview' => true], JSON_UNESCAPED_UNICODE),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => 10,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ];
        $caFile = $config['max_ca_file'] ?? '';
        if (is_string($caFile) && $caFile !== '' && is_file($caFile)) $options[CURLOPT_CAINFO] = $caFile;
        curl_setopt_array($curl, $options);
        $result = curl_exec($curl);
        $status = curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        $decoded = is_string($result) ? json_decode($result, true) : null;
        $delivered = $delivered || ($status === 200 && is_array($decoded) && isset($decoded['message']));
    }
}

if (!$attempted) reply(503, 'Delivery unavailable');
if (!$delivered) reply(502, 'Delivery unavailable');
reply(200, '');
