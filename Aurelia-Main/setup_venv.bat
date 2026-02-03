@echo off
echo Creating virtual environment...
python -m venv .venv

echo Activating virtual environment...
call .venv\Scripts\activate

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing requirements from requirements.txt...
pip install -r requirements.txt

echo Installing extra requirements from extra-req.txt...
pip install -r extra-req.txt

echo.
echo Setup complete! To run Aurelia, make sure the .venv is activated and run:
echo python main_chat.py
echo.
pause
