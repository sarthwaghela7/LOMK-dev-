import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import './App.css'

const sections = [
  { items: ['Dashboard'] },
  { items: ['Follow Up Calls Assign'] },
  { title: 'Calls Assign', items: ['Direct Calls Assign', 'Website Leads Assign', 'SM Leads Assign', 'Reviews Assign', 'Missed Call Assign'] },
  { title: 'Case Summary', items: ['Assigned Cases Summary', 'Assigned Reviews Summary'] },
  { title: 'Business Tracking', items: ['Business Operations', 'Business Analytics'] },
  { title: 'Performance', items: ['Agent Call Quality Score', 'Agent Performance Report', 'Call Update'] },
  { title: 'Business Insights', items: ['Enquiries & Trends', 'RFE Enquiries', 'I-485 Enquiries'] },
  { title: 'Platform & User Management', items: ['Call Count Check', 'User Management', 'Platform Setting'] },
]

const slugify = (label) => label.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
const pages = sections.flatMap((section) => section.items.map((label) => ({
  label,
  path: `/${section.title ? `${slugify(section.title)}/` : ''}${slugify(label)}`,
})))
const pathFor = (label) => pages.find((page) => page.label === label).path

const navIcons = {
  'Dashboard': 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  'Follow Up Calls Assign': 'M21 11a9 9 0 1 1-3-6 M21 3v6h-6 M12 7v5l3 2',
  'Calls Assign': 'M22 16v3a2 2 0 0 1-2 2C10 21 3 14 3 4a2 2 0 0 1 2-2h3l2 6-3 2a15 15 0 0 0 7 7l2-3z',
  'Case Summary': 'M9 5H4v16h16V5h-5 M9 3h6v4H9z M8 12h8 M8 16h6',
  'Business Tracking': 'M3 3v18h18 M7 16v-5 M12 16V7 M17 16V4',
  'Performance': 'M4 17a9 9 0 1 1 16 0 M12 13l5-5 M5 17h14',
  'Business Insights': 'M9 18h6 M10 22h4 M8 14a7 7 0 1 1 8 0l-1 4H9z',
  'Platform & User Management': 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M17 4a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-4',
}
function NavIcon({ label }) {
  return <svg className="nav-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={navIcons[label]} /></svg>
}

export default function App() {
  return <BrowserRouter><Workspace /></BrowserRouter>
}

function Workspace() {
  const location = useLocation()
  const active = pages.find((page) => page.path === location.pathname)?.label
  const [collapsed, setCollapsed] = useState(false)
  const [openMenu, setOpenMenu] = useState(null)
  const [headerPanel, setHeaderPanel] = useState(null)
  const [dark, setDark] = useState(() => {
    try { return localStorage.getItem('autoflint-theme') === 'dark' } catch { return false }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    try { localStorage.setItem('autoflint-theme', dark ? 'dark' : 'light') } catch { /* Storage may be unavailable. */ }
  }, [dark])

  return (
    <div className={`app ${collapsed ? 'collapsed' : ''}`}>
      <header className="header">
        <button className="brand" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} aria-controls="sidebar"><span>AutoFlint</span></button>
        <div className="header-actions">
          <button className="icon-button" aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'} aria-pressed={dark} title={dark ? 'Switch to light mode' : 'Switch to dark mode'} onClick={() => setDark(!dark)}>
            <svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{dark ? <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></> : <path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z"/>}</svg>
          </button>
          <button className="icon-button" aria-label="Notifications" aria-expanded={headerPanel === 'notifications'} onClick={() => setHeaderPanel(headerPanel === 'notifications' ? null : 'notifications')}>
            <svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>
          </button>
          <button className="icon-button" aria-label="Profile" aria-expanded={headerPanel === 'profile'} onClick={() => setHeaderPanel(headerPanel === 'profile' ? null : 'profile')}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="9" r="3.5"/><path d="M5 19c1-7 13-7 14 0"/></svg>
          </button>
        </div>
        {headerPanel && <><button className="panel-dismiss" aria-label="Close panel" onClick={() => setHeaderPanel(null)}/><section className="header-panel" aria-label={headerPanel === 'notifications' ? 'Notifications' : 'Profile'}><h2>{headerPanel === 'notifications' ? 'Notifications' : 'Profile'}</h2><p>{headerPanel === 'notifications' ? 'No new notifications.' : 'AutoFlint'}</p></section></>}

      </header>
      <aside className="sidebar" id="sidebar">
        <nav aria-label="Main navigation">
          {sections.map((section) => (
            <section className="nav-group" key={section.title || section.items[0]}>
              {section.title ? (
                <div className="nav-parent" onMouseEnter={() => setOpenMenu(section.title)} onMouseLeave={() => setOpenMenu(null)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpenMenu(null) }}>
                  <button className={`nav-item parent-trigger ${section.items.includes(active) ? 'group-active' : ''}`} aria-label={section.title} title={collapsed ? section.title : undefined} aria-haspopup="true" aria-expanded={openMenu === section.title} onFocus={() => setOpenMenu(section.title)} onClick={() => setOpenMenu(openMenu === section.title ? null : section.title)}>
                    <NavIcon label={section.title}/><span className="nav-label">{section.title}</span><span className="chevron" aria-hidden="true">›</span>
                  </button>
                  <div className="submenu" hidden={openMenu !== section.title} aria-label={section.title}>
                    {section.items.map((item) => (
                      <Link key={item} to={pathFor(item)} className={`nav-item ${active === item ? 'selected' : ''}`} aria-current={active === item ? 'page' : undefined} onClick={() => setOpenMenu(null)}>{item}</Link>
                    ))}
                  </div>
                </div>
              ) : section.items.map((item) => (
                <Link key={item} to={pathFor(item)} className={`nav-item ${active === item ? 'selected' : ''}`} aria-label={item} title={collapsed ? item : undefined} aria-current={active === item ? 'page' : undefined} onClick={() => setOpenMenu(null)}><NavIcon label={item}/><span className="nav-label">{item}</span></Link>
              ))}
            </section>
          ))}
        </nav>
      </aside>
      <main key={location.pathname}>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          {sections.filter((section) => section.title).map((section) => (
            <Route key={section.title} path={`/${slugify(section.title)}`} element={<Navigate to={pathFor(section.items[0])} replace />} />
          ))}
          {pages.map((page) => <Route key={page.path} path={page.path} element={assignmentPages[page.label] ? <AssignmentPage title={page.label} config={assignmentPages[page.label]} /> : <h1>{page.label}</h1>} />)}
          <Route path="*" element={<h1>Page not found</h1>} />
        </Routes>
      </main>
    </div>
  )
}

const followUpDropdowns = [
  ['leadType', 'Lead type', ['Direct Calls', 'Leads Calls', 'SM Leads']],
  ['websiteLeadType', 'Website lead type', ['Organic leads', 'Ad Leads']],
  ['smLeadType', 'SM lead type', ['LinkedIn', 'IG', 'FB']],
  ['reviews', 'Reviews', ['Success - GE', 'Success - PC']],
  ['caseType', 'Case type', ['I-485', 'I-130', 'RFE', 'K1', 'K2', 'H1B', 'H4EAD']],
  ['clientType', 'Client type', ['New Clients', 'Existing Clients']],
  ['followUpType', 'Follow up type', ['General Follow Up', 'PC Follow Up', 'Retainer Follow Up']],
  ['touchpointType', 'Touchpoint type', ['1st call follow up - D2', '2nd call follow up - D5', '3rd call follow up - D10']],
]

const directColumns = ['Sl No', 'Date', 'Timestamp', 'Transcript', 'Audio', 'Needs', 'Enquiry Level', 'Call ID', 'Client ID', 'Client Name', 'Client Ph No', 'Client Email', 'Client Type', 'Call Duration', 'Lead Source', 'Urgency Flag', 'Caseworker Assigned', 'Enquiry Question', 'Call Summary', 'Assign Owner', 'Action']
const leadColumns = ['Sl No', 'Date', 'Source', 'Needs', 'Enquiry Level', 'Client Name', 'Client Ph No', 'Client Email', 'Client Type', 'Lead Category', 'Lead ID', 'Enquiry Question', 'Assign Owner', 'Action']
const conversionStatuses = ['Converted PC', 'Converted Retainer', 'Converted PC + Retainer', 'Paid PC', 'Paid Retainer', 'Paid PC + Retainer']
const assignmentPages = {
  'Business Operations': {
    summary: true, business: true, tableTitle: 'Business operations records', rowCount: 6,
    dropdowns: [
      ['businessFlagged', 'Business flagged', ['PC Potential', 'Business Opportunity']],
      ...followUpDropdowns.filter(([key]) => key === 'caseType'),
      ['conversionStatus', 'Conversion status', conversionStatuses],
    ],
  },
  'Assigned Cases Summary': { summary: true, dropdowns: [['completionStatus', 'Completion status', ['Open', 'Completed']], ...followUpDropdowns.filter(([key]) => ['caseType', 'clientType', 'websiteLeadType', 'smLeadType'].includes(key))], tableTitle: 'Case summary records', rowCount: 6 },
  'Assigned Reviews Summary': { summary: true, dropdowns: [['completionStatus', 'Completion status', ['Open', 'Completed']], ...followUpDropdowns.filter(([key]) => ['reviews', 'clientType'].includes(key))], tableTitle: 'Review summary records', rowCount: 6 },
  'Follow Up Calls Assign': { followUp: true, tableTitle: 'Follow-up records', rowCount: 6 },
  'Direct Calls Assign': { columns: directColumns, tableTitle: 'Direct call records', rowCount: 6 },
  'Website Leads Assign': { dropdowns: [['websiteLeadType', 'Website lead type', ['Organic leads', 'Ad Leads']]], columns: leadColumns, tableTitle: 'Website lead records', rowCount: 4 },
  'SM Leads Assign': { dropdowns: [['smLeadType', 'SM lead type', ['LinkedIn', 'IG', 'FB']]], columns: leadColumns, tableTitle: 'Social media lead records', rowCount: 4 },
  'Reviews Assign': { columns: directColumns, tableTitle: 'Review records', rowCount: 6, note: 'Only calls with case status Success PC or Success GE.' },
}

function AssignmentPage({ title, config }) {
  const [categories, setCategories] = useState({})
  const [filters, setFilters] = useState({ from: '', to: '', assigned: false, unassigned: false, l1: false, l2: false, l3: false })
  const [applied, setApplied] = useState(false)
  function update(key, value) {
    setFilters((current) => ({ ...current, [key]: value }))
    setApplied(false)
  }
  function apply(event) {
    event.preventDefault()
    setApplied(true)
  }
  return (
    <>
      <h1>{title}</h1>
      {config.note && <p className="assignment-note">{config.note}</p>}
      <form className="filter-belt" onSubmit={apply}>

        <div className="filter-controls">
          <fieldset className="filter-group date-group"><legend>Date range</legend><div className="date-range">
            <label className="date-filter"><span>From date</span><input type="date" value={filters.from} max={filters.to || undefined} onChange={(event) => update('from', event.target.value)} /></label>
            <span className="date-arrow" aria-hidden="true">→</span>
            <label className="date-filter"><span>To date</span><input type="date" value={filters.to} min={filters.from || undefined} onChange={(event) => update('to', event.target.value)} /></label>
          </div></fieldset>
          {!config.summary && <><label className="dropdown-filter"><span>Assignment</span><select value={filters.assigned ? 'assigned' : filters.unassigned ? 'unassigned' : ''} onChange={(event) => { setFilters((current) => ({ ...current, assigned: event.target.value === 'assigned', unassigned: event.target.value === 'unassigned' })); setApplied(false) }}><option value="">All assignments</option><option value="assigned">Assigned</option><option value="unassigned">Unassigned</option></select></label>
          <label className="dropdown-filter"><span>Enquiry level</span><select value={filters.l1 ? 'l1' : filters.l2 ? 'l2' : filters.l3 ? 'l3' : ''} onChange={(event) => { setFilters((current) => ({ ...current, l1: event.target.value === 'l1', l2: event.target.value === 'l2', l3: event.target.value === 'l3' })); setApplied(false) }}><option value="">All levels</option><option value="l1">L1 Enquiries</option><option value="l2">L2 Enquiries</option><option value="l3">L3 Enquiries</option></select></label></>}
        </div>
        <div className="compact-filter-actions"><button type="button" className="reset-filters" onClick={() => { setFilters({ from: '', to: '', assigned: false, unassigned: false, l1: false, l2: false, l3: false }); setCategories({}); setApplied(false) }}>Reset</button><button className="apply-filters" type="submit">{applied ? 'Applied ✓' : 'Apply'}</button></div>
      </form>
      {!config.summary && <section className="assignment-section" aria-labelledby="assignment-heading">
        <h2 id="assignment-heading" className="sr-only">Team assignments</h2>
        <div className="assignment-counts">
        {[['Gokul', 30], ['Priyanka', 33], ['Christobel', 25], ['Gracy', 25], ['Edgin', 30], ['Shristhi', 35]].map(([name, count]) => (
          <article className="agent-count" key={name}>
            <div className="agent-identity"><span className="agent-avatar" aria-hidden="true">{name[0]}</span><span>{name}</span></div>
            <div className="agent-capacity"><strong>{count}<span> / 45</span></strong></div>
            <div className="capacity-track" role="meter" aria-label={`${name} assignments`} aria-valuenow={count} aria-valuemin={0} aria-valuemax={45}><span style={{ width: `${count / 45 * 100}%` }} /></div>
          </article>
        ))}
        </div>
      </section>}
      {(config.followUp || config.dropdowns) && <section className={`category-filters ${config.summary ? 'summary-category-filters' : !config.followUp ? 'source-category-filters' : ''}`} aria-label="Record categories">
        {(config.followUp ? followUpDropdowns : config.dropdowns).filter(([key]) => (!config.followUp || key !== 'websiteLeadType' || categories.leadType === 'Leads Calls') && (!config.followUp || key !== 'smLeadType' || categories.leadType === 'SM Leads')).map(([key, label, options]) => (
          <label className="dropdown-filter" key={key}>
            <span>{label}</span>
            <select value={categories[key] || ''} onChange={(event) => { setCategories((current) => ({ ...current, [key]: event.target.value, ...(key === 'leadType' ? { websiteLeadType: '', smLeadType: '' } : {}) })); setApplied(false) }}>
              <option value="">{key === 'reviews' ? 'All reviews' : key === 'completionStatus' ? 'All statuses' : `All ${label.toLowerCase()}s`}</option>
              {options.map((option) => <option value={option} key={option}>{option}</option>)}
            </select>
          </label>
        ))}
      </section>}
      <RecordsTable columns={config.business ? [...tableColumns.filter((column) => !['Assign Owner', 'Action'].includes(column)), 'Conversion Status', 'Paid Amount', 'Action'] : config.columns} title={config.tableTitle} rowCount={config.rowCount} />
    </>
  )
}

const tableColumns = [
  'Sl No', 'Date', 'Open Duration', 'Timestamp', 'Transcript', 'Audio', 'Needs', 'Enquiry Level',
  'Lead Score', 'PC Potential', 'Retainer Potential', 'Business Opportunity', 'Call ID', 'Client ID',
  'Lead ID', 'Client Ph No', 'Client Email', 'Agent', 'Ext Number', 'Direction', 'Call Type', 'Duration',
  'Client Name', 'Client Type', 'Lead Source', 'Urgency Flag', 'Case Type', 'Case Worker Assigned',
  'Call Outcome', 'Enquiry Question', 'Call Summary', 'Action Item', 'Action Due Date', 'Action Status',
  'Dependency', 'Blocker Comment', 'Follow Up Required', 'Follow Up Type', 'Touchpoint Status',
  'Client Response Status', 'Follow Up Date', 'Case Status Change', 'Date of Case Status Change',
  'Case Status Verification Comment', 'Assign Owner', 'Action',
]
const tableOptions = {
  'Conversion Status': conversionStatuses,
  'Case Status Change': ['Open', 'Completed', 'Initial email sent', 'Success - GE', 'Success - PC', 'Dropped'],
  'Follow Up Required': ['Yes', 'No'],
  'Follow Up Type': ['General Follow Up', 'PC Follow Up', 'Retainer Follow Up'],
  'Touchpoint Status': ['1st call follow up - D2', '2nd call follow up - D5', '3rd call follow up - D10'],
  'Client Response Status': ['No response', 'Need time / Will call back', 'Call Back later', 'Provide more info', 'Share PC link', 'Initiate Retainer Sign Up'],
  'Lead Category': ['EB/Employment DC', 'Family GC', 'EB Employment / DC', 'General Query', 'RFE / Case Rescue', 'Student', 'Visitor / B1, B2', 'Work Visa'],
  'Client Type': ['New Clients', 'Existing Clients'],
  'Lead Source': ['website', 'organic', 'ads', 'google', 'metaads', 'ig', 'LinkedIn', 'FB'],
  'Enquiry Level': ['L1 Enquiries', 'L2 Enquiries', 'L3 Enquiries'],
  'Assign Owner': ['Gokul', 'Priyanka', 'Christobel', 'Gracy', 'Edgin', 'Shristhi'],
}
function RecordsTable({ columns = tableColumns, title = 'Follow-up records', rowCount = 6 }) {
  const tableScrollRef = useRef(null)
  const holdTimer = useRef(null)
  const holdOrigin = useRef(null)
  const [pinned, setPinned] = useState(() => columns.slice(0, 3))
  const [pinMessage, setPinMessage] = useState('')
  const [compactViewport, setCompactViewport] = useState(() => window.innerWidth <= 650)
  useEffect(() => {
    const resize = () => setCompactViewport(window.innerWidth <= 650)
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); clearTimeout(holdTimer.current) }
  }, [])
  const widthFor = (column) => {
    const index = columns.indexOf(column)
    return index === 0 ? (compactViewport ? 40 : 60) : index === 1 ? (compactViewport ? 100 : 140) : index === 2 ? (compactViewport ? 80 : 120) : 160
  }
  const pinnedColumns = columns.filter((column) => pinned.includes(column))
  function pinStyle(column) {
    return { width: widthFor(column), minWidth: widthFor(column), maxWidth: widthFor(column), ...(pinned.includes(column) ? { left: pinnedColumns.slice(0, pinnedColumns.indexOf(column)).reduce((sum, item) => sum + widthFor(item), 0) } : {}) }
  }
  function togglePin(column) {
    setPinned((current) => current.includes(column) ? current.filter((item) => item !== column) : [...current, column])
    setPinMessage(`${column} ${pinned.includes(column) ? 'unpinned' : 'pinned'}`)
  }
  function cancelHold() { clearTimeout(holdTimer.current); holdOrigin.current = null }
  function startHold(event, column) {
    if (event.button !== 0) return
    cancelHold()
    holdOrigin.current = { x: event.clientX, y: event.clientY }
    holdTimer.current = setTimeout(() => { togglePin(column); holdOrigin.current = null }, 600)
  }
  const [rows, setRows] = useState(Array.from({ length: 25 }, () => ({})))
  const [visibleHeight, setVisibleHeight] = useState(null)
  useEffect(() => {
    const container = tableScrollRef.current
    let frame
    const fitRows = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const top = container.getBoundingClientRect().top
        const headerHeight = container.querySelector('thead').getBoundingClientRect().height
        const cellHeight = container.querySelector('tbody tr').getBoundingClientRect().height
        const available = Math.max(headerHeight + cellHeight * rowCount + 2, window.innerHeight - top - 44)
        const count = Math.min(25, Math.max(rowCount, Math.floor((available - headerHeight - 2) / cellHeight)))
        setVisibleHeight(headerHeight + count * cellHeight + 2)
      })
    }
    const observer = new ResizeObserver(fitRows)
    observer.observe(container.parentElement)
    window.addEventListener('resize', fitRows)
    fitRows()
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', fitRows)
    }
  }, [rowCount])
  const [savedRows, setSavedRows] = useState([])
  function edit(rowIndex, column, value) {
    setRows((current) => current.map((row, index) => index === rowIndex ? { ...row, [column]: value } : row))
    setSavedRows((current) => current.filter((index) => index !== rowIndex))
  }
  return <section className="followup-table-section" aria-labelledby="records-title">
    <div className="records-heading"><h2 id="records-title">{title}</h2><span>Long press a header to pin or unpin <span aria-hidden="true">↔</span></span></div>
    <div className="table-scroll" style={visibleHeight ? { height: visibleHeight, maxHeight: visibleHeight } : undefined} ref={tableScrollRef} role="region" aria-label={`${title}, ${pinned.length} pinned columns`} tabIndex={0}>
      <table className="followup-table"><caption className="sr-only">{title}. Long press a column header to toggle pinning, or focus it and press Enter or Space. Rows are blank for entry; Submit saves changes in this page only.</caption>
        <thead><tr>{columns.map((column) => <th key={column} scope="col" style={pinStyle(column)} className={`${pinned.includes(column) ? 'frozen pinned-column' : ''} ${['Lead Score', 'PC Potential', 'Retainer Potential', 'Business Opportunity'].includes(column) ? 'score-column' : ''} ${['Touchpoint Status', 'Client Response Status', 'Follow Up Date', 'Case Status Change', 'Date of Case Status Change', 'Case Status Verification Comment'].includes(column) ? 'status-column' : ''}`}>
          <button className="column-pin-toggle" aria-pressed={pinned.includes(column)} aria-label={`${column}, ${pinned.includes(column) ? 'pinned' : 'unpinned'}. Long press or press Enter to toggle.`} onPointerDown={(event) => startHold(event, column)} onPointerUp={cancelHold} onPointerLeave={cancelHold} onPointerCancel={cancelHold} onPointerMove={(event) => { if (holdOrigin.current && Math.hypot(event.clientX - holdOrigin.current.x, event.clientY - holdOrigin.current.y) > 8) cancelHold() }} onContextMenu={(event) => event.preventDefault()} onKeyDown={(event) => { if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) { event.preventDefault(); togglePin(column) } }}>{column}{pinned.includes(column) && <svg className="pin-indicator" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m9 3 6 0-1 6 4 4v2H6v-2l4-4zM12 15v7"/></svg>}</button>
        </th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{columns.map((column, index) => <td key={column} style={pinStyle(column)} className={pinned.includes(column) ? 'frozen pinned-column' : ''}>
          {column === 'Sl No' ? <span className="row-number">{String(rowIndex + 1).padStart(2, '0')}</span>
            : column === 'Action' ? <button className={`row-submit ${savedRows.includes(rowIndex) ? 'row-saved' : ''}`} onClick={() => setSavedRows((current) => [...new Set([...current, rowIndex])])}>{savedRows.includes(rowIndex) ? 'Saved ✓' : 'Submit'}</button>
            : tableOptions[column] ? <select aria-label={`${column}, row ${rowIndex + 1}`} value={row[column] || ''} onChange={(event) => edit(rowIndex, column, event.target.value)}><option value="">Select…</option>{tableOptions[column].map((option) => <option key={option}>{option}</option>)}</select>
            : <input aria-label={`${column}, row ${rowIndex + 1}`} type={['Date', 'Action Due Date', 'Follow Up Date', 'Date of Case Status Change'].includes(column) ? 'date' : column === 'Paid Amount' ? 'number' : 'text'} min={column === 'Paid Amount' ? 0 : undefined} step={column === 'Paid Amount' ? '0.01' : undefined} value={row[column] || ''} onChange={(event) => edit(rowIndex, column, event.target.value)} />}
        </td>)}</tr>)}</tbody>
      </table>
    </div>
    <span className="sr-only" role="status">{pinMessage}</span>
  </section>
}
