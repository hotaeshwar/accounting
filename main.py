from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import FileResponse
from typing import Optional, List, Literal
from pydantic import BaseModel, validator
from datetime import datetime, date
import pandas as pd
import sqlite3
import uuid
from pathlib import Path
import tempfile
import os
import shutil
from enum import Enum

# Create app
app = FastAPI(title="Simple Office Accounting API")

# Create database directory
DATABASE_DIR = Path("./data")
DATABASE_DIR.mkdir(exist_ok=True)
DATABASE_PATH = DATABASE_DIR / "accounting.db"

# Create exports directory for preserving Excel files
EXPORTS_DIR = Path("./exports")
EXPORTS_DIR.mkdir(exist_ok=True)

# Define office categories as an Enum
class OfficeCategory(str, Enum):
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

# Models for request/response
class EntryBase(BaseModel):
    date: str
    client: str
    description: str
    category: str  # Will validate against OfficeCategory
    amount: float
    payment: str
    tax: float = 0.0
    notes: str = ""
    is_income: bool = False  # New field to distinguish income from expenses
    
    # Validate that category is one of the predefined categories
    @validator('category')
    def validate_category(cls, v):
        try:
            return OfficeCategory(v)
        except ValueError:
            # Allow custom categories, but warn user
            return v

class EntryCreate(EntryBase):
    pass

class Entry(EntryBase):
    id: str
    invoice: str
    month: str

class EntriesResponse(BaseModel):
    entries: List[Entry]
    total_income: float
    total_expense: float
    total_tax: float

# New model for salesperson work assignments
class WorkAssignmentBase(BaseModel):
    date: str
    salesperson: str
    client: str
    task: str
    status: str = "Pending"  # Default status
    due_date: str
    priority: str = "Medium"
    notes: str = ""

class WorkAssignmentCreate(WorkAssignmentBase):
    pass

class WorkAssignment(WorkAssignmentBase):
    id: str
    created_on: str
    month: str

# Database setup
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if entries table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entries'")
    table_exists = cursor.fetchone() is not None
    
    if not table_exists:
        # Create entries table with is_income column
        cursor.execute('''
        CREATE TABLE entries (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            invoice TEXT NOT NULL,
            client TEXT,
            description TEXT NOT NULL,
            category TEXT,
            amount REAL NOT NULL,
            payment TEXT,
            tax REAL,
            notes TEXT,
            month TEXT,
            is_income BOOLEAN DEFAULT 0
        )
        ''')
    else:
        # Check if is_income column exists in entries table
        cursor.execute("PRAGMA table_info(entries)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        # Add is_income column if it doesn't exist
        if 'is_income' not in column_names:
            cursor.execute("ALTER TABLE entries ADD COLUMN is_income BOOLEAN DEFAULT 0")
    
    # Table to track last month cleared
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS month_tracking (
        id INTEGER PRIMARY KEY,
        last_cleared_month TEXT
    )
    ''')
    
    # New table for salesperson work assignments
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS work_assignments (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        salesperson TEXT NOT NULL,
        client TEXT NOT NULL,
        task TEXT NOT NULL,
        status TEXT NOT NULL,
        due_date TEXT NOT NULL,
        priority TEXT NOT NULL,
        notes TEXT,
        created_on TEXT NOT NULL,
        month TEXT NOT NULL
    )
    ''')
    
    conn.commit()
    conn.close()

# Thread-safe database connection
def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# Generate a new invoice number
def generate_invoice_number(db_conn, date_str):
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        year = date_obj.year
        month = date_obj.month
        
        prefix = f"{year}{month:02d}-"
        
        cursor = db_conn.cursor()
        cursor.execute("SELECT invoice FROM entries WHERE invoice LIKE ?", (prefix + "%",))
        existing_invoices = cursor.fetchall()
        
        max_num = 0
        for row in existing_invoices:
            invoice = row["invoice"]
            if invoice.startswith(prefix):
                try:
                    num = int(invoice[len(prefix):])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        
        return f"{prefix}{max_num + 1:04d}"
    except Exception as e:
        raise RuntimeError(f"Failed to generate invoice number: {str(e)}")

# Get current month in "Month Year" format
def get_current_month():
    return datetime.now().strftime('%B %Y')

# Check if month has changed and clear old entries
async def check_month_transition(db: sqlite3.Connection):
    current_month = get_current_month()
    cursor = db.cursor()
    
    # Check what was the last cleared month
    cursor.execute("SELECT last_cleared_month FROM month_tracking LIMIT 1")
    row = cursor.fetchone()
    
    last_cleared_month = row["last_cleared_month"] if row else None
    
    # If first run or month has changed
    if not last_cleared_month or last_cleared_month != current_month:
        # Before clearing, export previous month's data
        if last_cleared_month:
            # Get entries from previous month
            cursor.execute("SELECT * FROM entries")
            rows = cursor.fetchall()
            
            if rows:
                # Convert to DataFrame
                entries = [dict(row) for row in rows]
                df = pd.DataFrame(entries)
                
                # Save to Excel
                date_parts = last_cleared_month.split()
                filename = f"accounting_{date_parts[1]}_{datetime.strptime(date_parts[0], '%B').month:02d}.xlsx"
                export_path = EXPORTS_DIR / filename
                
                with pd.ExcelWriter(export_path, engine="xlsxwriter") as writer:
                    df.to_excel(writer, sheet_name=last_cleared_month, index=False)
                    
                    # Format headers
                    workbook = writer.book
                    worksheet = writer.sheets[last_cleared_month]
                    
                    header_format = workbook.add_format({
                        'bold': True,
                        'text_wrap': True,
                        'valign': 'top',
                        'bg_color': '#D9E1F2',
                        'border': 1
                    })
                    
                    for col_num, value in enumerate(df.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                        
                    # Set column widths
                    for i, col in enumerate(df.columns):
                        worksheet.set_column(i, i, max(len(col) + 2, 15))
            
            # Also get and export work assignments from previous month
            cursor.execute("SELECT * FROM work_assignments")
            work_rows = cursor.fetchall()
            
            if work_rows:
                # Convert to DataFrame
                work_entries = [dict(row) for row in work_rows]
                work_df = pd.DataFrame(work_entries)
                
                # Save to Excel
                work_filename = f"work_assignments_{date_parts[1]}_{datetime.strptime(date_parts[0], '%B').month:02d}.xlsx"
                work_export_path = EXPORTS_DIR / work_filename
                
                with pd.ExcelWriter(work_export_path, engine="xlsxwriter") as writer:
                    work_df.to_excel(writer, sheet_name=last_cleared_month, index=False)
                    
                    # Format headers
                    workbook = writer.book
                    worksheet = writer.sheets[last_cleared_month]
                    
                    header_format = workbook.add_format({
                        'bold': True,
                        'text_wrap': True,
                        'valign': 'top',
                        'bg_color': '#D9E1F2',
                        'border': 1
                    })
                    
                    for col_num, value in enumerate(work_df.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                        
                    # Set column widths
                    for i, col in enumerate(work_df.columns):
                        worksheet.set_column(i, i, max(len(col) + 2, 15))
            
            # Clear all entries
            cursor.execute("DELETE FROM entries")
            cursor.execute("DELETE FROM work_assignments")
        
        # Update last cleared month
        if not last_cleared_month:
            cursor.execute("INSERT INTO month_tracking (last_cleared_month) VALUES (?)", (current_month,))
        else:
            cursor.execute("UPDATE month_tracking SET last_cleared_month = ?", (current_month,))
            
        db.commit()

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    # Open connection to check month transition
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    await check_month_transition(conn)
    conn.close()

# 1. Add Entry Endpoint
@app.post("/api/entries/")
async def add_entry(entry_data: EntryCreate, db: sqlite3.Connection = Depends(get_db)):
    """Add a new entry with auto-generated ID and invoice"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        # Generate unique ID
        entry_id = str(uuid.uuid4())
            
        # Set month to current month
        month = get_current_month()
        
        # Auto-generate invoice number
        invoice = generate_invoice_number(db, entry_data.date)
            
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO entries (id, date, invoice, client, description, category, 
                               amount, payment, tax, notes, month, is_income)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, entry_data.date, invoice, entry_data.client, entry_data.description,
             entry_data.category, entry_data.amount, entry_data.payment, entry_data.tax, 
             entry_data.notes, month, 1 if entry_data.is_income else 0)
        )
        db.commit()
        return {"success": True, "id": entry_id, "invoice": invoice}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add entry: {str(e)}")

# 2. Get Entries Endpoint
@app.get("/api/entries/")
async def get_entries(db: sqlite3.Connection = Depends(get_db)):
    """Get all entries for the current month"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        cursor = db.cursor()
        cursor.execute("SELECT * FROM entries")
        rows = cursor.fetchall()
        
        entries = []
        total_income = 0.0
        total_expense = 0.0
        total_tax = 0.0
        
        for row in rows:
            entry = dict(row)
            entries.append(entry)
            
            # Calculate totals based on is_income flag
            amount = float(entry["amount"])
            tax = float(entry["tax"]) if entry["tax"] else 0.0
            
            if entry["is_income"]:
                total_income += amount
            else:
                total_expense += amount
                
            total_tax += tax
        
        return {
            "month": get_current_month(),
            "entries": entries,
            "total_income": total_income,
            "total_expense": total_expense,
            "total_tax": total_tax,
            "categories": {entry["category"]: sum(float(e["amount"]) for e in entries if e["category"] == entry["category"]) 
                        for entry in entries if entry["category"]}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# New endpoint to get available categories
@app.get("/api/categories/")
async def get_categories():
    """Get list of predefined office categories"""
    return {"categories": [category.value for category in OfficeCategory]}

# 3. Download Excel Endpoint
@app.get("/api/download-excel/")
async def download_excel(db: sqlite3.Connection = Depends(get_db)):
    """Generate and download Excel file with current month's data"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        current_month = get_current_month()
        
        cursor = db.cursor()
        cursor.execute("SELECT * FROM entries")
        rows = cursor.fetchall()
        
        if not rows:
            raise HTTPException(status_code=404, detail=f"No data available for current month")
        
        # Convert to DataFrame
        entries = [dict(row) for row in rows]
        df = pd.DataFrame(entries)
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            temp_filename = tmp.name
            
        # Save to Excel with formatting
        with pd.ExcelWriter(temp_filename, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name=current_month, index=False)
            
            # Get workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets[current_month]
            
            # Add formatting
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'bg_color': '#D9E1F2',
                'border': 1
            })
            
            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Set column widths
            for i, col in enumerate(df.columns):
                worksheet.set_column(i, i, max(len(col) + 2, 15))
        
        # Return the file
        date_parts = current_month.split()
        filename = f"accounting_{date_parts[1]}_{datetime.strptime(date_parts[0], '%B').month:02d}.xlsx"
        
        return FileResponse(
            path=temp_filename,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate Excel file: {str(e)}")

# 4. Add Salesperson Work Assignment Endpoint
@app.post("/api/work-assignments/")
async def add_work_assignment(work_data: WorkAssignmentCreate, db: sqlite3.Connection = Depends(get_db)):
    """Add a new work assignment for a salesperson"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        # Generate unique ID
        assignment_id = str(uuid.uuid4())
            
        # Set month to current month
        month = get_current_month()
        
        # Set creation timestamp
        created_on = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO work_assignments (id, date, salesperson, client, task, 
                                        status, due_date, priority, notes, created_on, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (assignment_id, work_data.date, work_data.salesperson, work_data.client, 
             work_data.task, work_data.status, work_data.due_date, work_data.priority,
             work_data.notes, created_on, month)
        )
        db.commit()
        return {"success": True, "id": assignment_id, "created_on": created_on}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add work assignment: {str(e)}")

# 5. Get Work Assignments Endpoint
@app.get("/api/work-assignments/")
async def get_work_assignments(salesperson: Optional[str] = None, db: sqlite3.Connection = Depends(get_db)):
    """Get all work assignments for the current month, optionally filtered by salesperson"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        cursor = db.cursor()
        
        if salesperson:
            cursor.execute("SELECT * FROM work_assignments WHERE salesperson = ? ORDER BY priority, due_date", 
                          (salesperson,))
        else:
            cursor.execute("SELECT * FROM work_assignments ORDER BY salesperson, priority, due_date")
            
        rows = cursor.fetchall()
        
        assignments = []
        for row in rows:
            assignment = dict(row)
            assignments.append(assignment)
        
        # Group assignments by status
        status_groups = {}
        for assignment in assignments:
            status = assignment["status"]
            if status not in status_groups:
                status_groups[status] = []
            status_groups[status].append(assignment)
        
        return {
            "month": get_current_month(),
            "assignments": assignments,
            "status_summary": {status: len(items) for status, items in status_groups.items()},
            "total_assignments": len(assignments)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# 6. Update Work Assignment Status Endpoint
@app.put("/api/work-assignments/{assignment_id}")
async def update_work_assignment(
    assignment_id: str, 
    status: str, 
    notes: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    """Update the status and optionally notes of a work assignment"""
    try:
        cursor = db.cursor()
        
        # Check if assignment exists
        cursor.execute("SELECT * FROM work_assignments WHERE id = ?", (assignment_id,))
        assignment = cursor.fetchone()
        
        if not assignment:
            raise HTTPException(status_code=404, detail=f"Work assignment with ID {assignment_id} not found")
        
        # Update the assignment
        if notes:
            cursor.execute(
                "UPDATE work_assignments SET status = ?, notes = ? WHERE id = ?",
                (status, notes, assignment_id)
            )
        else:
            cursor.execute(
                "UPDATE work_assignments SET status = ? WHERE id = ?",
                (status, assignment_id)
            )
            
        db.commit()
        
        return {"success": True, "message": f"Work assignment updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update work assignment: {str(e)}")

# 7. Download Work Assignments Excel Endpoint
@app.get("/api/download-work-excel/")
async def download_work_excel(salesperson: Optional[str] = None, db: sqlite3.Connection = Depends(get_db)):
    """Generate and download Excel file with current month's work assignments"""
    try:
        # Check if month transition needed
        await check_month_transition(db)
        
        current_month = get_current_month()
        
        cursor = db.cursor()
        
        if salesperson:
            cursor.execute("SELECT * FROM work_assignments WHERE salesperson = ?", (salesperson,))
        else:
            cursor.execute("SELECT * FROM work_assignments")
            
        rows = cursor.fetchall()
        
        if not rows:
            raise HTTPException(status_code=404, detail=f"No work assignments available for current month")
        
        # Convert to DataFrame
        assignments = [dict(row) for row in rows]
        df = pd.DataFrame(assignments)
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            temp_filename = tmp.name
            
        # Save to Excel with formatting
        with pd.ExcelWriter(temp_filename, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name=current_month, index=False)
            
            # Get workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets[current_month]
            
            # Add formatting
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'bg_color': '#D9E1F2',
                'border': 1
            })
            
            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Set column widths
            for i, col in enumerate(df.columns):
                worksheet.set_column(i, i, max(len(col) + 2, 15))
        
        # Return the file
        date_parts = current_month.split()
        filename = f"work_assignments_{date_parts[1]}_{datetime.strptime(date_parts[0], '%B').month:02d}.xlsx"
        if salesperson:
            filename = f"{salesperson}_{filename}"
        
        return FileResponse(
            path=temp_filename,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate Excel file: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)