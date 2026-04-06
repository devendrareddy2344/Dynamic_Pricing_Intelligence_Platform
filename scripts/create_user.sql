CREATE USER IF NOT EXISTS 'pricing_user'@'localhost' IDENTIFIED WITH mysql_native_password BY 'Veera@123';
GRANT ALL PRIVILEGES ON dynamic_pricing.* TO 'pricing_user'@'localhost';
FLUSH PRIVILEGES;
SELECT user, host, plugin FROM mysql.user WHERE user='pricing_user';
