Attribute VB_Name = "PrepareICAPAutomation"
Sub PrepareICAPAutomation()
    ' === Setup ===
    Dim wsAgile As Worksheet, wsDL_Icaps As Worksheet, wsDL_Primos As Worksheet, wsOppUsed As Worksheet
    Dim wsDLStatusList As Worksheet  ' NEW: For DL_Status lookup
    Dim tblAgile As ListObject
    Dim dictDLStatus As Object, filteredRows As Collection
    Dim dictDLStatusComments As Object  ' NEW: For DL_Status -> Comment mapping
    Dim i As Long, lastRowDL As Long
    Dim icapID As String, agileStatus As String, internalStatus As String
    Dim statusCol As Long, newRowNew As Long, newRowUpdate As Long
    Dim cViewRef As String, jdeContractID As String
    Dim fileComment As String
    Dim colIndex As Long

    ' === Refresh Queries ===
    Dim lo As ListObject, ws As Worksheet
    For Each ws In ThisWorkbook.Worksheets
        For Each lo In ws.ListObjects
            On Error Resume Next
            lo.Refresh
            On Error GoTo 0
        Next lo
    Next ws
    Application.CalculateUntilAsyncQueriesDone
    ' === Set References ===
    Set wsAgile = Sheets("AgileFile")
    Set wsDL_Icaps = Sheets("DL_ICAP")
    Set wsDL_Primos = Sheets("DL_Primos")
    Set wsOppUsed = Sheets("Opportunities_used")
    Set tblAgile = wsAgile.ListObjects(1)

    ' === NEW: Load DL_Status lookup sheet ===
    On Error Resume Next
    Set wsDLStatusList = Sheets("DL_Status_List")
    On Error GoTo 0

    ' === NEW: Build DL_Status -> Comment Dictionary ===
    Set dictDLStatusComments = CreateObject("Scripting.Dictionary")
    If Not wsDLStatusList Is Nothing Then
        Dim lastRowStatusList As Long
        Dim dlStatusColLookup As Long, commentColLookup As Long

        dlStatusColLookup = FindColumn(wsDLStatusList, "DL_Status", 1)
        commentColLookup = FindColumn(wsDLStatusList, "comment", 1)

        If dlStatusColLookup > 0 And commentColLookup > 0 Then
            lastRowStatusList = wsDLStatusList.Cells(wsDLStatusList.rows.Count, dlStatusColLookup).End(xlUp).Row
            For i = 2 To lastRowStatusList
                Dim dlStatusKey As String, commentValue As String
                dlStatusKey = Trim(wsDLStatusList.Cells(i, dlStatusColLookup).Value)
                commentValue = Trim(wsDLStatusList.Cells(i, commentColLookup).Value)
                If dlStatusKey <> "" And Not dictDLStatusComments.Exists(dlStatusKey) Then
                    dictDLStatusComments.Add dlStatusKey, commentValue
                End If
            Next i
        End If
    End If


    ' === Clean Reason for Approval column (remove line breaks) ===
    Dim statusIdx As Long, reasonIdx As Long
    statusIdx = FindColumn(wsAgile, "Status", 1)
    reasonIdx = FindColumn(wsAgile, "Reason For Approval", 1)
    If statusIdx = 0 Or reasonIdx = 0 Then
        MsgBox "Required columns not found in AgileFile.", vbCritical
        Exit Sub
    End If

    For i = 1 To tblAgile.ListRows.Count
        Dim reasonText As String
        reasonText = tblAgile.ListRows(i).Range.Cells(1, reasonIdx).Value
        reasonText = Replace(reasonText, vbCrLf, " ")
        reasonText = Replace(reasonText, vbCr, " ")
        reasonText = Replace(reasonText, vbLf, " ")
        Do While InStr(reasonText, "  ") > 0
            reasonText = Replace(reasonText, "  ", " ")
        Loop
        tblAgile.ListRows(i).Range.Cells(1, reasonIdx).Value = Trim(reasonText)
    Next i

    ' === Remove rows with Status = New and blank Reason For Approval ===
    For i = tblAgile.ListRows.Count To 1 Step -1
        With tblAgile.ListRows(i).Range
            If Trim(.Cells(1, statusIdx).Value) = "New" And Trim(.Cells(1, reasonIdx).Value) = "" Then
                tblAgile.ListRows(i).Delete
            End If
        End With
    Next i

    ' === Build Dictionary: ICAP_ID -> InternalStatus and ICAP_Date ===
    Set dictDLStatus = CreateObject("Scripting.Dictionary")
    Dim dictDLDate As Object
    Set dictDLDate = CreateObject("Scripting.Dictionary")
    Dim icapCol As Long, internalStatusCol As Long, dlIcapDateCol As Long
    icapCol = FindColumn(wsDL_Icaps, "Title", 1)

    internalStatusCol = FindColumn(wsDL_Icaps, "Agile_Status", 1)
    dlIcapDateCol = FindColumn(wsDL_Icaps, "ICAP_Date", 1)

    If icapCol = 0 Or internalStatusCol = 0 Then
        MsgBox "Sub PrepareICAPAutomation() > Required columns not found in DL_ICAP.", vbCritical
        Exit Sub
    End If
    lastRowDL = wsDL_Icaps.Cells(wsDL_Icaps.rows.Count, icapCol).End(xlUp).Row
    For i = 2 To lastRowDL
        icapID = Trim(wsDL_Icaps.Cells(i, icapCol).Value)
        internalStatus = Trim(wsDL_Icaps.Cells(i, internalStatusCol).Value)
        If Not dictDLStatus.Exists(icapID) Then dictDLStatus.Add icapID, internalStatus
        ' Store ICAP_Date from DL_ICAP if column exists
        If dlIcapDateCol > 0 And Not dictDLDate.Exists(icapID) Then
            dictDLDate.Add icapID, wsDL_Icaps.Cells(i, dlIcapDateCol).Value
        End If
    Next i

    ' === Add or Find Update_Status Column ===
    On Error Resume Next
    statusCol = FindColumn(wsAgile, "Update_Status", 1)
    If statusCol = 0 Then
        tblAgile.ListColumns.Add.Name = "Update_Status"
        statusCol = FindColumn(wsAgile, "Update_Status", 1)
    End If
    On Error GoTo 0

    ' === Get AgileFile Columns ===
    Dim icapIDCol As Long, agileStatusCol As Long, cViewRefCol As Long, jdeContractCol As Long
    Dim icapDateCol As Long
    icapIDCol = FindColumn(wsAgile, "ICAP ID", 1)
    agileStatusCol = FindColumn(wsAgile, "Status", 1)
    cViewRefCol = FindColumn(wsAgile, "C-View Ref", 1)
    jdeContractCol = FindColumn(wsAgile, "JDE Contract ID", 1)
    icapDateCol = FindColumn(wsAgile, "ICAP_Date", 1)
    If icapIDCol = 0 Or agileStatusCol = 0 Or cViewRefCol = 0 Or jdeContractCol = 0 Then
        MsgBox "Missing columns in AgileFile.", vbCritical
        Exit Sub
    End If

    ' === Mark Rows as New / Update / Old ===
    Dim r As ListRow
    For Each r In tblAgile.ListRows
        icapID = Trim(r.Range.Cells(1, icapIDCol).Value)
        agileStatus = Trim(r.Range.Cells(1, agileStatusCol).Value)
        If dictDLStatus.Exists(icapID) Then
            internalStatus = dictDLStatus(icapID)
            If agileStatus <> internalStatus Then
                r.Range.Cells(1, statusCol).Value = "Update"
            Else
                r.Range.Cells(1, statusCol).Value = "Old"
            End If
        ElseIf agileStatus = "Cancelled" Then
            r.Range.Cells(1, statusCol).Value = "Old"
        Else
            r.Range.Cells(1, statusCol).Value = "New"
        End If
    Next r

    ' === Create/Clear NewICAP and UpdateICAP sheets ===
    On Error Resume Next
    Application.DisplayAlerts = False
    Worksheets("NewICAP").Delete
    Worksheets("UpdateICAP").Delete
    Application.DisplayAlerts = True
    On Error GoTo 0
    Dim wsNewICAP As Worksheet, wsUpdateICAP As Worksheet
    Set wsNewICAP = Sheets.Add(After:=wsAgile)
    wsNewICAP.Name = "NewICAP"
    Set wsUpdateICAP = Sheets.Add(After:=wsNewICAP)
    wsUpdateICAP.Name = "UpdateICAP"

    ' === Define Column Mapping (Output Header -> Source Column Name) ===
    Dim columnMapping As Object
    Set columnMapping = CreateObject("Scripting.Dictionary")
    ' Map output headers to source columns
    columnMapping.Add "ICAP_ID", "ICAP ID"
    columnMapping.Add "ICAP_Date", "ICAP_Date"
    columnMapping.Add "InternalStatus", "INTERNAL_STATUS_PLACEHOLDER"
    columnMapping.Add "Opportunity_number", "C-View Ref"
    columnMapping.Add "JDE_ContractID", "JDE Contract ID"
    columnMapping.Add "Reason_for_Approval", "Reason For Approval"
    columnMapping.Add "Agile_Status", "Status"
    columnMapping.Add "Validation", "VALIDATION_PLACEHOLDER"
    columnMapping.Add "Gross_Revenue", "Gross revenue"
    columnMapping.Add "CAPEX", "CAPEX"
    columnMapping.Add "PRIMO", "PRIMO_PLACEHOLDER"
    columnMapping.Add "PSD", "PSD_PLACEHOLDER"
    columnMapping.Add "DAF", "DAF_PLACEHOLDER"
    columnMapping.Add "Automation", "AUT_PLACEHOLDER"
    columnMapping.Add "Duration", "Duration"
    columnMapping.Add "Country", "Country"
    columnMapping.Add "Start_Date", "Start Date"
    columnMapping.Add "DL_status", "DL_STATUS_PLACEHOLDER"
    columnMapping.Add "Comments", "COMMENTS_PLACEHOLDER"
    columnMapping.Add "Sector", "Sector"
    columnMapping.Add "Index", "Index"
    columnMapping.Add "Requester", "Requester"
    columnMapping.Add "Customer", "Customer"
    columnMapping.Add "Region", "Region"
    columnMapping.Add "ICAP_Type", "ICAP Type"

    ' === Create Headers in Order ===
    Dim outputHeaders As Variant
    outputHeaders = Array("ICAP_ID", "ICAP_Date", "InternalStatus", "Opportunity_number", _
                         "JDE_ContractID", "Reason_for_Approval", "Agile_Status", "Validation", _
                         "Gross_Revenue", "CAPEX", "PRIMO", "PSD", "DAF", "Automation", _
                         "Duration", "Country", "Start_Date", "DL_status", "Comments", _
                         "Sector", "Index", "Requester", "Customer", "Region", "ICAP_Type")
    For i = 0 To UBound(outputHeaders)
        wsNewICAP.Cells(1, i + 1).Value = outputHeaders(i)
        wsUpdateICAP.Cells(1, i + 1).Value = outputHeaders(i)
    Next i
    newRowNew = 2
    newRowUpdate = 2

    ' FIXED COLUMN INDEXES: For robust placement of calculated values
    Dim primoColIdx As Long, psdColIdx As Long, dafColIdx As Long, autColIdx As Long
    Dim icapDateColIdx As Long
    primoColIdx = FindColumn(wsNewICAP, "PRIMO", 1)
    psdColIdx = FindColumn(wsNewICAP, "PSD", 1)
    dafColIdx = FindColumn(wsNewICAP, "DAF", 1)
    autColIdx = FindColumn(wsNewICAP, "Automation", 1)
    icapDateColIdx = FindColumn(wsNewICAP, "ICAP_Date", 1)

    ' === Build Opportunities_used list for lookup ===
    Dim oppUsedDict As Object
    Set oppUsedDict = CreateObject("Scripting.Dictionary")
    Dim lastRowOpp As Long, oppVal As String
    lastRowOpp = wsOppUsed.Cells(wsOppUsed.rows.Count, 1).End(xlUp).Row
    For i = 1 To lastRowOpp
        oppVal = Trim(wsOppUsed.Cells(i, 1).Value)
        If Len(oppVal) > 0 Then
            If Not oppUsedDict.Exists(oppVal) Then oppUsedDict.Add oppVal, True
        End If
    Next i

    ' === Populate Sheets ===
    Dim dlStatusVal As String, gsdStatusVal As String, validationVal As String
    Dim primoVal As String, psdVal As String, dafVal As String, autVal As String
    Dim targetRow As Long, targetSheet As Worksheet
    Dim outputHeader As Variant, sourceColName As String, sourceColIndex As Long

    For Each r In tblAgile.ListRows
        Dim statusVal As String: statusVal = r.Range.Cells(1, statusCol).Value
        If statusVal = "New" Or statusVal = "Update" Then
            ' Determine target sheet and row
            If statusVal = "New" Then
                Set targetSheet = wsNewICAP
                targetRow = newRowNew
            Else
                Set targetSheet = wsUpdateICAP
                targetRow = newRowUpdate
            End If

            cViewRef = Trim(r.Range.Cells(1, cViewRefCol).Value)
            jdeContractID = Trim(r.Range.Cells(1, jdeContractCol).Value)
            fileComment = ""

            ' *** C-VIEW VALIDATION LOGIC ***
            Dim cViewValid As Boolean
            cViewValid = True

            If Len(cViewRef) = 0 Then
                cViewValid = False ' 1. Blank
            ElseIf Not IsNumeric(cViewRef) Then
                cViewValid = False ' 2. Contains letters/non-numeric
            ElseIf Len(cViewRef) < 5 Or Len(cViewRef) > 9 Then
                cViewValid = False ' 3. Invalid length
            End If

            If cViewValid Then
                Set filteredRows = FilterDLPrimos(wsDL_Primos, cViewRef)

                ' Calculate PRIMO, PSD, DAF, AUT
                ' PRIMO uses the prefix check "PRIMOWHS_SD"
                primoVal = EvaluateFileType(filteredRows, "PRIMOWHS_SD", True, fileComment)
                psdVal = EvaluateFileType(filteredRows, "PSD", False, "")
                dafVal = EvaluateFileType(filteredRows, "DAF", False, "")
                autVal = EvaluateFileType(filteredRows, "AUT", False, "")
            Else
                ' Set to blank if C-View Ref is invalid
                primoVal = ""
                psdVal = ""
                dafVal = ""
                autVal = ""
            End If
            ' *** END C-VIEW VALIDATION LOGIC ***
            ' === DL_Status Logic (Updated to check for blank validation status) ===
            If primoVal = "V" And psdVal = "V" And (dafVal = "V" Or dafVal = "N" Or dafVal = "") And (autVal = "V" Or autVal = "N" Or autVal = "") Then
                dlStatusVal = "Validated"
            ElseIf Not cViewValid Then
                dlStatusVal = "ICAP created without reference"
            ElseIf Not oppUsedDict.Exists(cViewRef) Then
                dlStatusVal = "Project not included in Digital Library"
            ElseIf primoVal = "N" Then
                dlStatusVal = "Missing PRIMO"
            ElseIf psdVal = "N" Or dafVal = "R" Or autVal = "R" Then
                dlStatusVal = "Pending Documentation"
            ElseIf primoVal = "Y" Or psdVal = "Y" Or dafVal = "Y" Or autVal = "Y" Then
                dlStatusVal = "Pending validation"
            Else
                dlStatusVal = "Error"
            End If

            ' === Validation Column ===
            Select Case dlStatusVal
                Case "Validated"
                    validationVal = "Y"
                Case "Pending validation", "Pending Documentation"
                    validationVal = "P"
                Case Else
                    validationVal = "N"
            End Select

            ' === GSD_Status Logic (InternalStatus) - CORRECTED: Incomplete based only on cViewValid ===
            Dim currentStatus As String
            currentStatus = Trim(r.Range.Cells(1, agileStatusCol).Value)

            If currentStatus = "Approved" Then
                gsdStatusVal = "Approved"
            ElseIf currentStatus = "Cancelled" Then
                gsdStatusVal = "Cancelled"
            ElseIf Not cViewValid Then
                gsdStatusVal = "Incomplete"
            Else
                gsdStatusVal = "Pending"
            End If
            ' === END GSD_Status Logic (InternalStatus) ===

            ' === Write data to target sheet in correct order ===
            For colIndex = 0 To UBound(outputHeaders)
                outputHeader = outputHeaders(colIndex)
                sourceColName = columnMapping(outputHeader)
                Select Case sourceColName
                    Case "ICAP_Date"
                        ' NEW LOGIC: Set ICAP_Date based on sheet type
                        If statusVal = "New" Then
                            ' For NewICAP sheet: use Today()
                            targetSheet.Cells(targetRow, icapDateColIdx).Value = Date
                        Else
                            ' For UpdateICAP sheet: use value from DL_ICAP
                            icapID = Trim(r.Range.Cells(1, icapIDCol).Value)
                            If dictDLDate.Exists(icapID) Then
                                targetSheet.Cells(targetRow, icapDateColIdx).Value = dictDLDate(icapID)
                            End If
                        End If
                    Case "INTERNAL_STATUS_PLACEHOLDER"
                        targetSheet.Cells(targetRow, colIndex + 1).Value = gsdStatusVal
                    Case "VALIDATION_PLACEHOLDER"
                        targetSheet.Cells(targetRow, colIndex + 1).Value = validationVal
                    Case "PRIMO_PLACEHOLDER"
                        targetSheet.Cells(targetRow, primoColIdx).Value = primoVal
                    Case "PSD_PLACEHOLDER"
                        targetSheet.Cells(targetRow, psdColIdx).Value = psdVal
                    Case "DAF_PLACEHOLDER"
                        targetSheet.Cells(targetRow, dafColIdx).Value = dafVal
                    Case "AUT_PLACEHOLDER"
                        targetSheet.Cells(targetRow, autColIdx).Value = autVal
                    Case "DL_STATUS_PLACEHOLDER"
                        targetSheet.Cells(targetRow, colIndex + 1).Value = dlStatusVal
                    Case "COMMENTS_PLACEHOLDER"
                        ' NEW: Check DL_Status lookup first for NewICAP tab only, fallback to fileComment
                        If statusVal = "New" And dlStatusVal <> "" And dictDLStatusComments.Exists(dlStatusVal) Then
                            targetSheet.Cells(targetRow, colIndex + 1).Value = dictDLStatusComments(dlStatusVal)
                        Else
                            targetSheet.Cells(targetRow, colIndex + 1).Value = fileComment
                        End If
                    Case Else
                        sourceColIndex = FindColumn(wsAgile, sourceColName, 1)
                        If sourceColIndex > 0 Then
                            targetSheet.Cells(targetRow, colIndex + 1).Value = r.Range.Cells(1, sourceColIndex).Value
                        End If
                End Select
            Next colIndex
            If statusVal = "New" Then
                newRowNew = newRowNew + 1
            Else
                newRowUpdate = newRowUpdate + 1
            End If
        End If
    Next r

    ' === Move to New Workbook and convert sheets to tables ===
    Dim wbNew As Workbook
    Dim tbl As ListObject
    Dim wsNewCopy As Worksheet, wsUpdateCopy As Worksheet
    Dim newFileName As String
    Dim savePath As String
    ' CHANGED: File name now uses US date format MM-DD-YYYY
    newFileName = "ICAP_Update_" & Format(Date, "MM-DD-YYYY") & ".xlsx"
    savePath = ThisWorkbook.Path & "\" & newFileName
    Set wbNew = Workbooks.Add
    wsNewICAP.Copy After:=wbNew.Sheets(wbNew.Sheets.Count)
    Set wsNewCopy = wbNew.Sheets(wbNew.Sheets.Count)
    wsNewCopy.Name = "NewICAP"
    Set tbl = wsNewCopy.ListObjects.Add(xlSrcRange, wsNewCopy.UsedRange, , xlYes)
    tbl.Name = "NewICAP"
    wsUpdateICAP.Copy After:=wbNew.Sheets(wbNew.Sheets.Count)
    Set wsUpdateCopy = wbNew.Sheets(wbNew.Sheets.Count)
    wsUpdateCopy.Name = "UpdateICAP"
    Set tbl = wsUpdateCopy.ListObjects.Add(xlSrcRange, wsUpdateCopy.UsedRange, , xlYes)
    tbl.Name = "UpdateICAP"
    ' === Create Instructions Sheet ===
    Dim wsInstructions As Worksheet
    Set wsInstructions = wbNew.Sheets.Add(After:=wbNew.Sheets(wbNew.Sheets.Count))
    wsInstructions.Name = "Validate and Upload"
    With wsInstructions
        .Cells(2, 2).Value = "ICAP Update File"
        .Cells(2, 2).Font.Size = 16
        .Cells(2, 2).Font.Bold = True
        ' CHANGED: Display date uses US format MM/DD/YYYY
        .Cells(4, 2).Value = "Created: " & Format(Date, "MM/DD/YYYY")
        .Cells(5, 2).Value = "File: " & newFileName
        .Cells(7, 2).Value = "Click the button below to save this file to SharePoint:"
        .Cells(9, 2).Value = "SharePoint Path:"
        .Cells(10, 2).Value = "Global-CL-Digital > Primo & Applications Team > ICAP_Update"
        .Cells(10, 2).Font.Color = RGB(0, 0, 255)
        .Cells(10, 2).Font.Italic = True
        .Columns("A:A").ColumnWidth = 2
        .Columns("B:E").ColumnWidth = 25
    End With
    Dim btn As Button
    Set btn = wsInstructions.Buttons.Add(wsInstructions.Cells(12, 2).Left, _
                                         wsInstructions.Cells(12, 2).Top, 150, 30)
    btn.Caption = "Save to SharePoint"
    btn.OnAction = "SaveToSharePoint"
    Dim sht As Worksheet
    Application.DisplayAlerts = False
    On Error Resume Next
    Set sht = wbNew.Sheets("Sheet1")
    If Not sht Is Nothing Then sht.Delete
    On Error GoTo 0
    Application.DisplayAlerts = True
    Application.DisplayAlerts = False
    On Error Resume Next
    wsNewICAP.Delete
    wsUpdateICAP.Delete
    On Error GoTo 0
    Application.DisplayAlerts = True
    Call FormatOutputSheets(wsNewCopy)
    Call FormatOutputSheets(wsUpdateCopy)
    Application.DisplayAlerts = False
    wbNew.SaveAs fileName:=savePath, FileFormat:=xlOpenXMLWorkbook
    Application.DisplayAlerts = True
    MsgBox "ICAP Automation complete. Output workbook saved as: " & newFileName, vbInformation
End Sub
' ---------------------------------------------------------------------------------------------------
' --- Helper Functions ---
Sub FormatOutputSheets(ws As Worksheet)
    Dim tbl As ListObject
    Dim col As ListColumn
    Dim colName As String
    Dim numberCols As Variant
    numberCols = Array("Duration", "Requester", "Gross_Revenue", "CAPEX")
    If ws.ListObjects.Count > 0 Then
        Set tbl = ws.ListObjects(1)

        ' Check if table has data rows - if not, skip formatting (not an error)
        If tbl.ListRows.Count > 0 Then
            Dim numCol As Variant
            For Each numCol In numberCols
                On Error Resume Next
                Set col = tbl.ListColumns(CStr(numCol))
                If Not col Is Nothing Then
                    If Not col.DataBodyRange Is Nothing Then
                        col.DataBodyRange.NumberFormat = "###0.00"
                    End If
                End If
                Set col = Nothing
                On Error GoTo 0
            Next numCol
            ' CHANGED: Date format now uses US format mm/dd/yyyy
            For Each col In tbl.ListColumns
                colName = LCase(col.Name)
                If InStr(colName, "date") > 0 Then
                    If Not col.DataBodyRange Is Nothing Then
                        col.DataBodyRange.NumberFormat = "mm/dd/yyyy"
                    End If
                End If
            Next col
        End If
    End If
End Sub
Function FindColumn(ws As Worksheet, headerName As String, headerRow As Long) As Long
    Dim c As Range
    For Each c In ws.rows(headerRow).Cells
        If Trim(UCase(c.Value)) = Trim(UCase(headerName)) Then
            FindColumn = c.Column
            Exit Function
        End If
    Next c
    FindColumn = 0
End Function
Function FilterDLPrimos(ws As Worksheet, refValue As String) As Collection
    Dim result As New Collection
    Dim lastRow As Long: lastRow = ws.Cells(ws.rows.Count, "A").End(xlUp).Row
    Dim i As Long, opCol As Long
    opCol = FindColumn(ws, "cr7ad_operationnumber", 1)
    If opCol = 0 Then Exit Function
    For i = 2 To lastRow
        If Trim(ws.Cells(i, opCol).Value) = refValue Then
            result.Add i
        End If
    Next i
    Set FilterDLPrimos = result
End Function
Function EvaluateFileType(rows As Collection, fileTypeName As String, captureComment As Boolean, ByRef commentOut As String) As String
    Dim ws As Worksheet: Set ws = Sheets("DL_Primos")
    Dim i As Variant, rowNum As Long
    Dim typeCol As Long, statusCol As Long, commentCol As Long
    typeCol = FindColumn(ws, "cr7ad_filetype", 1)
    statusCol = FindColumn(ws, "cr7ad_status", 1)
    commentCol = FindColumn(ws, "cr7ad_validationcomment", 1)
    EvaluateFileType = "N" ' Default value if file type is NOT found

    For Each i In rows
        rowNum = CLng(i)

        ' MODIFIED LOGIC: Check if the file type starts with fileTypeName (prefix match)
        If InStr(1, LCase(Trim(ws.Cells(rowNum, typeCol).Value)), LCase(fileTypeName), vbTextCompare) = 1 Then

            Dim fileStatus As String
            fileStatus = Trim(ws.Cells(rowNum, statusCol).Value)
            If captureComment Then commentOut = ws.Cells(rowNum, commentCol).Value
            Select Case fileStatus
                Case "Validated": EvaluateFileType = "V"
                Case "Rejected": EvaluateFileType = "N"
                Case "Pending": EvaluateFileType = "Y"
                Case Else: EvaluateFileType = "Y" ' Default for unrecognized status
            End Select
            Exit Function
        End If
    Next i
End Function
Function IsAlphaNumeric(str As String) As Boolean
    ' Checks if the string is alphanumeric (letters and digits only)
    Dim i As Long, ch As String, ascVal As Integer
    If Len(str) = 0 Then
        IsAlphaNumeric = False
        Exit Function
    End If
    For i = 1 To Len(str)
        ch = Mid(str, i, 1)
        ascVal = Asc(ch)
        If Not ((ascVal >= 48 And ascVal <= 57) Or _
                (ascVal >= 65 And ascVal <= 90) Or _
                (ascVal >= 97 And ascVal <= 122)) Then
            IsAlphaNumeric = False
            Exit Function
        End If
    Next i
    IsAlphaNumeric = True
End Function
Sub SaveToSharePoint()
    ' SharePoint path derived from browser URL
    Const SHAREPOINT_PATH As String = "https://cevalogisticsoffice365.sharepoint.com/sites/Global-CL-Digital/Shared Documents/General/Primo & Applications Team/Programs/ICAP Report/ICAP_Update/"
    Dim wb As Workbook
    Dim fileName As String
    Dim fullPath As String
    Set wb = ActiveWorkbook
    fileName = wb.Name
    fullPath = SHAREPOINT_PATH & fileName
    On Error GoTo ErrorHandler
    Application.DisplayAlerts = False
    wb.SaveAs fileName:=fullPath, FileFormat:=xlOpenXMLWorkbook
    Application.DisplayAlerts = True
    MsgBox "File successfully saved to SharePoint!" & vbCrLf & vbCrLf & _
           "Location: ICAP_Update folder" & vbCrLf & _
           "File: " & fileName, vbInformation, "Success"
    Exit Sub
ErrorHandler:
    Dim response As VbMsgBoxResult
    response = MsgBox("Unable to save directly to SharePoint." & vbCrLf & vbCrLf & _
                     "Error: " & Err.Description & vbCrLf & vbCrLf & _
                     "Would you like to open the SharePoint folder in your browser?" & vbCrLf & _
                     "You can then manually upload the file.", vbQuestion + vbYesNo, "Upload Alternative")
    If response = vbYes Then
        ActiveWorkbook.FollowHyperlink Address:="https://cevalogisticsoffice365.sharepoint.com/sites/Global-CL-Digital/Shared%20Documents/General/Primo%20%26%20Applications%20Team/Programs/ICAP%20Report/ICAP_Update", NewWindow:=True
        MsgBox "SharePoint folder opened in browser." & vbCrLf & vbCrLf & _
               "Please upload the file manually." & vbCrLf & vbCrLf & _
               "File location: " & wb.Path & "\" & fileName, vbInformation
    End If
End Sub
