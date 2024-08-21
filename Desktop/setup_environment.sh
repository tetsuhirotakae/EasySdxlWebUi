cat << EOF > setup_environment.sh
   #!/bin/bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   EOF

   chmod +x setup_environment.sh
   git add setup_environment.sh
   git commit -m "Add setup_environment.sh"
   git push origin main