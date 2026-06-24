import CrudPage from '../../components/CrudPage';
export default function Page(){return <CrudPage title="Campaigns" path="/campaigns" defaults={{company_id:'',name:'',industry:'',daily_lead_goal:25,daily_email_goal:25,status:'Active'}}/>}
