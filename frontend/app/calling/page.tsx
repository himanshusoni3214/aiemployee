import { AllstateCallingPanel } from '../../components/AllstateCallingPanel';
import { serverApi } from '../../lib/serverApi';

const fallback = {
  confirmation_required: 'PLACE INTERNAL TEST CALL',
  settings: {},
  health: { internal_test_ready: false, blockers: ['Calling API unavailable'] },
  attempts: [],
};

export default async function CallingPage() {
  const workspace = await serverApi<any>('/calling/allstate', fallback);
  return <AllstateCallingPanel initialWorkspace={workspace} />;
}
