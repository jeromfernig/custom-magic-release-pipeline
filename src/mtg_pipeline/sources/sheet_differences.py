"""Keyed Sheet comparisons, independent of physical row/column positions."""
from __future__ import annotations


def difference_formula(left="Cards", right="ChatGPT's copy of Cards", max_rows=1011):
    def sheet(name):
        return "'" + name.replace("'", "''") + "'"
    # Whole-row ranges include newly added columns; bound rows to the source grid.
    lhs = f"{sheet(left)}!1:{max_rows}"
    rhs = f"{sheet(right)}!1:{max_rows}".replace('"', '""')
    return (
        f'=LET(leftdata,{lhs},rightdata,INDIRECT("{rhs}"),'
        'leftheaders,INDEX(leftdata,1,0),rightheaders,INDEX(rightdata,1,0),'
        'leftids,CHOOSECOLS(leftdata,MATCH("id",leftheaders,0)),'
        'rightids,CHOOSECOLS(rightdata,MATCH("id",rightheaders,0)),'
        'allkeys,UNIQUE(TOCOL(VSTACK(leftids,rightids),1)),'
        'keys,FILTER(allkeys,(allkeys<>"")*(allkeys<>"id")),'
        'allheaders,UNIQUE(TOCOL(VSTACK(leftheaders,rightheaders),3)),'
        'fields,FILTER(allheaders,(allheaders<>"")*(allheaders<>"id")),'
        'MAKEARRAY(ROWS(keys)+1,ROWS(fields)+1,LAMBDA(rowindex,columnindex,'
        'IF(rowindex=1,IF(columnindex=1,"id",INDEX(fields,MAX(1,columnindex-1))),'
        'IF(columnindex=1,INDEX(keys,rowindex-1),LET('
        'key,INDEX(keys,rowindex-1),header,INDEX(fields,columnindex-1),'
        'leftrow,IFNA(MATCH(key,leftids,0),0),rightrow,IFNA(MATCH(key,rightids,0),0),'
        'leftcolumn,IFNA(MATCH(header,leftheaders,0),0),rightcolumn,IFNA(MATCH(header,rightheaders,0),0),'
        'oldvalue,IF(leftrow*leftcolumn=0,"",INDEX(leftdata,MAX(1,leftrow),MAX(1,leftcolumn))&""),'
        'newvalue,IF(rightrow*rightcolumn=0,"",INDEX(rightdata,MAX(1,rightrow),MAX(1,rightcolumn))&""),'
        'IF(OR(COUNTIF(leftids,key)>1,COUNTIF(rightids,key)>1),"Duplicate ID",'
        'IF(leftrow=0,"Only in copy",IF(rightrow=0,"Only in Cards",'
        'IF(oldvalue=newvalue,"",IF(oldvalue="","Only in copy",'
        'IF(newvalue="","Only in Cards","Different values"))))))))))))'
    )
