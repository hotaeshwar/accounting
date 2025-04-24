from fastapi import FastAPI, Depends, HTTPException, status, Header
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from enum import Enum
from datetime import datetime, timedelta
import sqlite3
import os
import jwt
import pandas as pd
import uuid
import calendar
from fastapi.middleware.cors import CORSMiddleware
# Constants
DATABASE_NAME = "expense_tracker.db"
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
EXCEL_EXPORT_DIRECTORY = "exports"

# Create exports directory if it doesn't exist
if not os.path.exists(EXCEL_EXPORT_DIRECTORY):
    os.makedirs(EXCEL_EXPORT_DIRECTORY)

# Initialize FastAPI app
app = FastAPI(title="Office Expense Tracker API",
              description="API for tracking office expenses with admin and guest user roles")
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
   allow_origins=[
        "http://localhost:5173",
        "https://xautrademeeting.com"
    ],    # React Vite's default port
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)
# Define expense categories
class ExpenseCategory(str, Enum):
    OFFICE_SUPPLIES = "Office Supplies"
    RENT = "Rent"
    UTILITIES = "Utilities"
    SALARY = "Salary"
    TRAVEL = "Travel"
    EQUIPMENT = "Equipment"
    SOFTWARE = "Software"
    MARKETING = "Marketing"
    INSURANCE = "Insurance"
    TAXES = "Taxes"
    MAINTENANCE = "Maintenance"
    TELECOMMUNICATIONS = "Telecommunications"
    CONSULTING = "Consulting"
    LEGAL = "Legal"
    MISCELLANEOUS = "Miscellaneous"

# Define user role
class UserRole(str, Enum):
    ADMIN = "admin"
    GUEST = "guest"

# Database setup
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Check if users table exists and has password column
    has_password_column = False
    try:
        cursor.execute("PRAGMA table_info(users)")
        columns = [info[1] for info in cursor.fetchall()]
        has_password_column = 'password' in columns
    except sqlite3.OperationalError:
        # Table doesn't exist
        pass

    # If the table exists but doesn't have the password column, it's better to
    # recreate the database to ensure all tables have the correct schema
    if os.path.exists(DATABASE_NAME) and not has_password_column:
        conn.close()
        os.remove(DATABASE_NAME)
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        print("Recreating database with correct schema")

    # Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL
    )
    ''')

    # Expenses table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        description TEXT,
        date_created TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    # Archived expenses table for historical data
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS archived_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        description TEXT,
        date_created TEXT NOT NULL,
        archive_date TEXT NOT NULL
    )
    ''')

    # Income table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT,
        amount REAL NOT NULL,
        date_created TEXT NOT NULL
    )
    ''')

    conn.commit()
    conn.close()

# Initialize database
init_db()

# Pydantic models
class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole

class User(BaseModel):
    id: int
    username: str
    role: UserRole

class LoginRequest(BaseModel):
    username: str
    password: str
    role: Optional[UserRole] = None  # Optional role for login

class TokenData(BaseModel):
    access_token: str
    token_type: str
    user_role: str

class TokenResponse(BaseModel):
    success: bool = True
    data: TokenData
    message: str = "Login successful"

class ExpenseBase(BaseModel):
    amount: float
    category: ExpenseCategory
    description: Optional[str] = None

class ExpenseCreate(ExpenseBase):
    pass

class Expense(ExpenseBase):
    id: int
    invoice_id: str
    user_id: int
    date_created: str

class ResponseModel(BaseModel):
    success: bool
    data: Optional[Any] = None
    message: Optional[str] = None

class CategoryItem(BaseModel):
    key: str
    name: str

class CategoriesResponse(BaseModel):
    success: bool = True
    data: List[CategoryItem]
    message: str = "Categories retrieved successfully"

class IncomeCreate(BaseModel):
    description: str
    amount: float

class ProfitLossData(BaseModel):
    total_income: float
    total_expenses: float
    net_profit_loss: float
    expenses_by_category: Dict[str, float]

# User authentication functions
def get_user(username: str):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, role FROM users WHERE username = ?", (username,))
    user_data = cursor.fetchone()
    conn.close()

    if user_data:
        return {"id": user_data[0], "username": user_data[1], "password": user_data[2], "role": user_data[3]}
    return None

def authenticate_user(username: str, password: str, role: Optional[UserRole] = None):
    user = get_user(username)
    if not user or user["password"] != password:
        return False

    # If role is specified, check if user has that role
    if role and user["role"] != role:
        return False

    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Authentication dependency that accepts tokens from multiple sources
async def get_current_user(
    authorization: Optional[str] = Header(None),
    x: Optional[str] = None,
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Get token from authorization header
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ")[1]

    # If not in header, try query parameter
    elif x:
        if x.startswith("Bearer "):
            token = x.split("Bearer ")[1]
        else:
            token = x  # Try using the raw value

    if not token:
        raise credentials_exception

    try:
        # Decode and validate the token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_id: int = payload.get("user_id")
        role: str = payload.get("role")

        if username is None or user_id is None:
            raise credentials_exception

    except jwt.PyJWTError as e:
        # Any JWT decode error results in authentication failure
        print(f"JWT decode error: {str(e)}")
        raise credentials_exception

    # Validate that the user exists in the database
    user = get_user(username)
    if user is None:
        raise credentials_exception

    return user

async def get_current_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user

# Helper functions
def generate_invoice_id():
    # Generate a timestamp-based UUID for invoice to ensure uniqueness
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_id = str(uuid.uuid4()).split('-')[0]  # Use part of UUID for brevity
    return f"INV-{timestamp}-{unique_id}"

# API endpoints
@app.post("/register", response_model=ResponseModel)
async def register(user: UserCreate):
    db_user = get_user(user.username)
    if db_user:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Username already registered"}
        )

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                    (user.username, user.password, user.role))
        user_id = cursor.lastrowid
        conn.commit()

        user_data = {"id": user_id, "username": user.username, "role": user.role}
        return {"success": True, "data": user_data, "message": "User registered successfully"}
    except Exception as e:
        conn.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

@app.post("/login", response_model=TokenResponse)
async def login(login_data: LoginRequest):
    user = authenticate_user(login_data.username, login_data.password, login_data.role)
    if not user:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "success": False,
                "message": "Invalid credentials or role"
            }
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "user_id": user["id"], "role": user["role"]},
        expires_delta=access_token_expires
    )

    token_data = {
        "access_token": access_token,
        "token_type": "bearer",
        "user_role": user["role"]
    }

    return {
        "success": True,
        "data": token_data,
        "message": "Login successful"
    }

@app.get("/categories", response_model=CategoriesResponse)
async def get_categories():
    # No authentication required for fetching categories
    categories = [{"key": category.name, "name": category.value} for category in ExpenseCategory]
    return {
        "success": True,
        "data": categories,
        "message": "Categories retrieved successfully"
    }

@app.post("/expenses", response_model=ResponseModel)
async def create_expense(expense: ExpenseCreate, current_user: dict = Depends(get_current_user)):
    invoice_id = generate_invoice_id()
    date_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO expenses (invoice_id, user_id, amount, category, description, date_created) VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, current_user["id"], expense.amount, expense.category, expense.description, date_created)
        )
        expense_id = cursor.lastrowid
        conn.commit()

        expense_data = {
            "id": expense_id,
            "invoice_id": invoice_id,
            "user_id": current_user["id"],
            "amount": expense.amount,
            "category": expense.category,
            "description": expense.description,
            "date_created": date_created
        }

        return {
            "success": True,
            "data": expense_data,
            "message": f"Expense created successfully with invoice ID: {invoice_id}"
        }
    except Exception as e:
        conn.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

@app.get("/expenses/my", response_model=ResponseModel)
async def list_my_expenses(current_user: dict = Depends(get_current_user)):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id, invoice_id, user_id, amount, category, description, date_created FROM expenses WHERE user_id = ?",
            (current_user["id"],)
        )
        expenses = []
        for row in cursor.fetchall():
            expenses.append(dict(row))

        return {"success": True, "data": expenses}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

@app.get("/expenses/all", response_model=ResponseModel)
async def list_all_expenses(current_user: dict = Depends(get_current_admin)):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT e.id, e.invoice_id, e.user_id, e.amount, e.category, e.description, e.date_created, u.username FROM expenses e JOIN users u ON e.user_id = u.id"
        )
        expenses = []
        for row in cursor.fetchall():
            expenses.append(dict(row))

        return {"success": True, "data": expenses}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

@app.get("/export/current-month", response_model=ResponseModel)
async def export_current_month_expenses(current_user: dict = Depends(get_current_user)):
    # Only admins can manually trigger export
    if current_user["role"] != UserRole.ADMIN:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"success": False, "message": "Not enough permissions"}
        )

    try:
        current_date = datetime.now()
        filename = export_expenses_to_excel(current_date.year, current_date.month)

        return {
            "success": True,
            "data": {"filename": os.path.basename(filename)},
            "message": "Report generated successfully"
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Export error: {str(e)}"}
        )

@app.get("/download/report/{filename}")
async def download_report(filename: str, current_user: dict = Depends(get_current_admin)):
    filepath = os.path.join(EXCEL_EXPORT_DIRECTORY, filename)
    if not os.path.exists(filepath):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Report file not found"}
        )

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/invoice/{invoice_id}", response_model=ResponseModel)
async def get_invoice_by_id(invoice_id: str, current_user: dict = Depends(get_current_user)):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Check if expense exists
        cursor.execute(
            "SELECT e.*, u.username, u.role FROM expenses e JOIN users u ON e.user_id = u.id WHERE e.invoice_id = ?",
            (invoice_id,)
        )
        expense = cursor.fetchone()

        if not expense:
            # Check archived expenses
            cursor.execute(
                "SELECT * FROM archived_expenses WHERE invoice_id = ?",
                (invoice_id,)
            )
            expense = cursor.fetchone()
            if not expense:
                conn.close()
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"success": False, "message": "Invoice not found"}
                )

        # Convert to dict
        expense_dict = dict(expense)

        # Check if user has permission to view this invoice
        if current_user["role"] != UserRole.ADMIN and expense_dict["user_id"] != current_user["id"]:
            conn.close()
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "Not authorized to view this invoice"}
            )

        return {"success": True, "data": expense_dict}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

# API endpoint for manual archive operation
@app.post("/archive/{year}/{month}", response_model=ResponseModel)
async def archive_month_expenses(year: int, month: int, current_user: dict = Depends(get_current_admin)):
    try:
        # Validate month and year
        if month < 1 or month > 12:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Invalid month. Must be between 1-12"}
            )

        if year < 2000 or year > datetime.now().year:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": f"Invalid year. Must be between 2000-{datetime.now().year}"}
            )

        # Export to Excel first (also performs archiving)
        filename = export_expenses_to_excel(year, month)

        return {
            "success": True,
            "data": {"filename": os.path.basename(filename)},
            "message": f"Expenses for {year}-{month:02d} exported and archived successfully"
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Archive operation failed: {str(e)}"}
        )

# Excel export and archiving functions
def export_expenses_to_excel(year, month):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Format dates for query
    start_date = f"{year}-{month:02d}-01"
    _, last_day = calendar.monthrange(year, month)
    end_date = f"{year}-{month:02d}-{last_day} 23:59:59"

    # Get expenses for the month
    cursor.execute(
        """
        SELECT e.id, e.invoice_id, e.amount, e.category, e.description, e.date_created,
               u.username, u.role
        FROM expenses e
        JOIN users u ON e.user_id = u.id
        WHERE e.date_created BETWEEN ? AND ?
        """,
        (start_date, end_date)
    )

    expenses = []
    for row in cursor.fetchall():
        expenses.append(dict(row))

    # Create DataFrame for Excel export
    if expenses:
        df = pd.DataFrame(expenses)

        # Create Excel writer with formatting
        filename = f"{EXCEL_EXPORT_DIRECTORY}/expenses_{year}_{month:02d}.xlsx"
        writer = pd.ExcelWriter(filename, engine='openpyxl')

        # Write data
        df.to_excel(writer, index=False, sheet_name='Expenses')

        # Get the workbook and worksheet objects
        workbook = writer.book
        worksheet = writer.sheets['Expenses']

        # Import openpyxl for Excel color formatting
        import openpyxl
        from openpyxl.styles import PatternFill

        # Add color formatting based on user role
        for idx, row in enumerate(df.itertuples(index=False), start=2):  # Start from row 2 (after header)
            cell_color = "E6F2FF" if row.role == "guest" else "FFE6E6"  # Light blue for guest, light red for admin
            for col in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=idx, column=col)
                cell.fill = PatternFill(start_color=cell_color, end_color=cell_color, fill_type="solid")

        # Add total row
        total_row = len(df) + 2
        worksheet.cell(row=total_row, column=1, value="Total")
        worksheet.cell(row=total_row, column=3, value=f"=SUM(C2:C{total_row-1})")

        # Add some formatting
        for col in worksheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(cell.value)
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column].width = adjusted_width

        # Save the file
        writer.close()

        # Archive expenses
        archive_expenses(year, month)

        return filename
    else:
        # Create empty Excel file
        filename = f"{EXCEL_EXPORT_DIRECTORY}/expenses_{year}_{month:02d}_empty.xlsx"
        pd.DataFrame(columns=['No expenses found for this period']).to_excel(filename, index=False)
        return filename

def archive_expenses(year, month):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Format dates for query
    start_date = f"{year}-{month:02d}-01"
    _, last_day = calendar.monthrange(year, month)
    end_date = f"{year}-{month:02d}-{last_day} 23:59:59"
    archive_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Move expenses to archive
    cursor.execute(
        """
        INSERT INTO archived_expenses
        (invoice_id, user_id, amount, category, description, date_created, archive_date)
        SELECT invoice_id, user_id, amount, category, description, date_created, ?
        FROM expenses
        WHERE date_created BETWEEN ? AND ?
        """,
        (archive_date, start_date, end_date)
    )

    # Delete archived expenses from main table
    cursor.execute(
        "DELETE FROM expenses WHERE date_created BETWEEN ? AND ?",
        (start_date, end_date)
    )

    conn.commit()
    conn.close()
@app.get("/archive/{month}/{year}", response_model=ResponseModel)
async def get_archived_expenses(month: int, year: int, current_user: dict = Depends(get_current_admin)):
    """
    Retrieve archived expenses for a specific month and year.
    Only admin users can access this endpoint.
    """
    try:
        # Validate month and year
        if month < 1 or month > 12:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Invalid month. Must be between 1-12"}
            )

        if year < 2000 or year > datetime.now().year:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": f"Invalid year. Must be between 2000-{datetime.now().year}"}
            )

        # Format dates for query
        start_date = f"{year}-{month:02d}-01"
        _, last_day = calendar.monthrange(year, month)
        end_date = f"{year}-{month:02d}-{last_day} 23:59:59"

        # Connect to database
        conn = sqlite3.connect(DATABASE_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Retrieve archived expenses for the specified month/year
        cursor.execute(
            """
            SELECT ar.id, ar.invoice_id, ar.user_id, ar.amount, ar.category,
                   ar.description, ar.date_created, ar.archive_date, u.username
            FROM archived_expenses ar
            JOIN users u ON ar.user_id = u.id
            WHERE ar.date_created BETWEEN ? AND ?
            ORDER BY ar.date_created
            """,
            (start_date, end_date)
        )

        archived_expenses = []
        for row in cursor.fetchall():
            archived_expenses.append(dict(row))

        conn.close()

        if not archived_expenses:
            return {
                "success": True,
                "data": [],
                "message": f"No archived expenses found for {year}-{month:02d}"
            }

        return {
            "success": True,
            "data": archived_expenses,
            "message": f"Retrieved {len(archived_expenses)} archived expenses for {year}-{month:02d}"
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"This feature is reserved for admin login personnel only"}
        )

@app.post("/income", response_model=ResponseModel)
async def create_income(income: IncomeCreate, current_user: dict = Depends(get_current_admin)):
    """
    Create an income entry. Only admin users can access this endpoint.
    """
    date_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO income (description, amount, date_created) VALUES (?, ?, ?)",
            (income.description, income.amount, date_created)
        )
        income_id = cursor.lastrowid
        conn.commit()

        income_data = {
            "id": income_id,
            "description": income.description,
            "amount": income.amount,
            "date_created": date_created
        }

        return {
            "success": True,
            "data": income_data,
            "message": "Income created successfully"
        }
    except Exception as e:
        conn.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Database error: {str(e)}"}
        )
    finally:
        conn.close()

@app.get("/profit-loss/current-month", response_model=ResponseModel)
async def get_current_month_profit_loss(current_user: dict = Depends(get_current_user)):
    """
    Calculate and retrieve profit/loss data for the current month.
    """
    now = datetime.now()
    start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    end_date = now.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    try:
        # Calculate total income
        cursor.execute(
            "SELECT SUM(amount) FROM income WHERE date_created BETWEEN ? AND ?",
            (start_date, end_date)
        )
        total_income = cursor.fetchone()[0] or 0.0

        # Calculate total expenses
        cursor.execute(
            "SELECT SUM(amount) FROM expenses WHERE date_created BETWEEN ? AND ?",
            (start_date, end_date)
        )
        total_expenses = cursor.fetchone()[0] or 0.0

        # Calculate expenses by category
        expenses_by_category = {}
        for category in ExpenseCategory:
            cursor.execute(
                "SELECT SUM(amount) FROM expenses WHERE category = ? AND date_created BETWEEN ? AND ?",
                (category.value, start_date, end_date)
            )
            category_total = cursor.fetchone()[0] or 0.0
            expenses_by_category[category.value] = category_total

        net_profit_loss = total_income - total_expenses

        profit_loss_data = ProfitLossData(
            total_income=total_income,
            total_expenses=total_expenses,
            net_profit_loss=net_profit_loss,
            expenses_by_category=expenses_by_category
        )

        return {
            "success": True,
            "data": profit_loss_data,
            "message": "Profit/Loss data retrieved successfully"
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Error retrieving profit/loss data: {str(e)}"}
        )
    finally:
        conn.close()

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
