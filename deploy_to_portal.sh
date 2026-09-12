#!/bin/bash
echo "Uploading update zip to the server..."
scp -o StrictHostKeyChecking=no brixen-crm-update.zip root@portal.brixenconsultants.com:/var/www/brixen-crm/

echo "Extracting files and restarting the portal service..."
ssh -o StrictHostKeyChecking=no root@portal.brixenconsultants.com "cd /var/www/brixen-crm && unzip -o brixen-crm-update.zip && systemctl restart brixen-crm"

echo "Done! The live portal is now updated."
