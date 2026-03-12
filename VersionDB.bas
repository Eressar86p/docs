Attribute VB_Name = "VersionDB"
' ============================================================
' MODULE: VersionDB
' PURPOSE: Version tracking database for PrepareICAPAutomation
' SHEET:   Version_History (created in ThisWorkbook on first run)
' ============================================================

' Current macro version — update this constant with every release
Public Const MACRO_VERSION As String = "1.0.0"

' Column indexes in the Version_History sheet (never change these)
Private Const COL_VERSION     As Long = 1  ' A - Version number  (e.g. "1.0.0")
Private Const COL_DATE        As Long = 2  ' B - Release date    (date the version was deployed)
Private Const COL_AUTHOR      As Long = 3  ' C - Author          (who made the change)
Private Const COL_MODULE      As Long = 4  ' D - Module          (which .bas file was affected)
Private Const COL_CHANGE_TYPE As Long = 5  ' E - Change type     (New Feature / Bug Fix / Refactor / Hotfix)
Private Const COL_DESCRIPTION As Long = 6  ' F - Description     (plain-language summary of what changed)

' -------------------------------------------------------
' SetupVersionHistory
'   Creates the Version_History sheet if it does not exist,
'   writes headers, and seeds the initial v1.0.0 record.
'   Safe to call multiple times — skips setup if sheet
'   already exists.
' -------------------------------------------------------
Sub SetupVersionHistory()
    Dim wsVH As Worksheet

    ' --- Check if sheet already exists ---
    On Error Resume Next
    Set wsVH = ThisWorkbook.Sheets("Version_History")
    On Error GoTo 0

    If Not wsVH Is Nothing Then
        MsgBox "Version_History sheet already exists. No action taken.", vbInformation
        Exit Sub
    End If

    ' --- Create sheet ---
    Set wsVH = ThisWorkbook.Sheets.Add(After:=ThisWorkbook.Sheets(ThisWorkbook.Sheets.Count))
    wsVH.Name = "Version_History"

    ' --- Write headers ---
    With wsVH
        .Cells(1, COL_VERSION).Value     = "Version"
        .Cells(1, COL_DATE).Value        = "Release_Date"
        .Cells(1, COL_AUTHOR).Value      = "Author"
        .Cells(1, COL_MODULE).Value      = "Module"
        .Cells(1, COL_CHANGE_TYPE).Value = "Change_Type"
        .Cells(1, COL_DESCRIPTION).Value = "Description"

        ' --- Header formatting ---
        With .Range(.Cells(1, 1), .Cells(1, COL_DESCRIPTION))
            .Font.Bold = True
            .Interior.Color = RGB(31, 73, 125)   ' Dark blue
            .Font.Color = RGB(255, 255, 255)      ' White text
        End With

        ' --- Column widths ---
        .Columns(COL_VERSION).ColumnWidth     = 12
        .Columns(COL_DATE).ColumnWidth        = 14
        .Columns(COL_AUTHOR).ColumnWidth      = 20
        .Columns(COL_MODULE).ColumnWidth      = 28
        .Columns(COL_CHANGE_TYPE).ColumnWidth = 18
        .Columns(COL_DESCRIPTION).ColumnWidth = 80

        ' --- Date format for Release_Date column ---
        .Columns(COL_DATE).NumberFormat = "mm/dd/yyyy"

        ' --- Freeze header row ---
        .Activate
        .Rows(2).Select
        ActiveWindow.FreezePanes = True
        .Cells(1, 1).Select
    End With

    ' --- Convert to a named table ---
    Dim tblVH As ListObject
    Set tblVH = wsVH.ListObjects.Add( _
        SourceType:=xlSrcRange, _
        Source:=wsVH.Range("A1:F1"), _
        XlListObjectHasHeaders:=xlYes)
    tblVH.Name = "tbl_VersionHistory"

    ' --- Seed initial version record ---
    Call AddVersionEntry( _
        pVersion     := "1.0.0", _
        pDate        := "03/12/2026", _
        pAuthor      := "Eressar86p", _
        pModule      := "PrepareICAPAutomation", _
        pChangeType  := "New Feature", _
        pDescription := "Initial release. " & _
            "Processes AgileFile vs DL_ICAP to produce NewICAP and UpdateICAP sheets. " & _
            "Includes C-View validation, DL_Status/GSD_Status logic, DL_Status_List comment " & _
            "lookup, PRIMO/PSD/DAF/AUT prefix-match evaluation, ICAP_Date handling " & _
            "(Today for New, preserved from DL_ICAP for Update), output workbook with " & _
            "formatted tables, Instructions sheet, and SaveToSharePoint button.")

    MsgBox "Version_History sheet created and seeded with v1.0.0.", vbInformation
End Sub

' -------------------------------------------------------
' AddVersionEntry
'   Appends one row to the Version_History table.
'   Call this every time you deploy a new version.
'
'   Parameters:
'     pVersion     - e.g. "1.1.0"
'     pDate        - release date as string "MM/DD/YYYY"
'     pAuthor      - name of developer
'     pModule      - module(s) changed (e.g. "PrepareICAPAutomation, VersionDB")
'     pChangeType  - "New Feature" | "Bug Fix" | "Refactor" | "Hotfix"
'     pDescription - plain-language summary
' -------------------------------------------------------
Sub AddVersionEntry(pVersion As String, pDate As String, pAuthor As String, _
                    pModule As String, pChangeType As String, pDescription As String)
    Dim wsVH As Worksheet
    Dim tblVH As ListObject
    Dim newRow As ListRow

    On Error Resume Next
    Set wsVH = ThisWorkbook.Sheets("Version_History")
    On Error GoTo 0

    If wsVH Is Nothing Then
        MsgBox "Version_History sheet not found. Run SetupVersionHistory() first.", vbCritical
        Exit Sub
    End If

    If wsVH.ListObjects.Count = 0 Then
        MsgBox "Version_History table (tbl_VersionHistory) not found.", vbCritical
        Exit Sub
    End If

    Set tblVH = wsVH.ListObjects("tbl_VersionHistory")
    Set newRow = tblVH.ListRows.Add

    With newRow.Range
        .Cells(1, COL_VERSION).Value     = pVersion
        .Cells(1, COL_DATE).Value        = CDate(pDate)
        .Cells(1, COL_CHANGE_TYPE).Value = pChangeType
        .Cells(1, COL_AUTHOR).Value      = pAuthor
        .Cells(1, COL_MODULE).Value      = pModule
        .Cells(1, COL_DESCRIPTION).Value = pDescription
    End With

    ' Alternate row shading for readability
    Dim i As Long
    For i = 1 To tblVH.ListRows.Count
        If i Mod 2 = 0 Then
            tblVH.ListRows(i).Range.Interior.Color = RGB(235, 241, 250)  ' Light blue
        Else
            tblVH.ListRows(i).Range.Interior.Color = RGB(255, 255, 255)  ' White
        End If
    Next i
End Sub
