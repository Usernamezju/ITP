const tencentHints: Record<string, string> = {
  'ResourceUnavailable.NotExist': '可能是服务未开通或计费状态异常；请联系平台维护人员',
  'ResourceUnavailable.InArrears': '平台建模服务暂不可用，请联系平台维护人员',
  'ResourceUnavailable.LowBalance': '平台建模服务暂不可用，请联系平台维护人员',
  'AuthFailure.InvalidSecretId': '平台建模服务连接异常，请联系平台维护人员',
  'AuthFailure.SignatureFailure': '平台建模服务连接异常，请联系平台维护人员',
  UnsupportedRegion: '平台建模服务暂不支持此请求，请联系平台维护人员',
  UnauthorizedOperation: '平台建模服务暂不可用，请联系平台维护人员',
  RequestLimitExceeded: '请求超过频率限制；请稍后再试',
};

const poseHints: Record<string, string> = {
  InvalidApiKey: '平台姿势编辑服务连接异常，请联系平台维护人员',
  invalid_api_key: '平台姿势编辑服务连接异常，请联系平台维护人员',
  'AccessDenied.Unpurchased': '平台姿势编辑服务暂不可用，请联系平台维护人员',
  ModelNotFound: '姿势编辑模型暂不可用，请选择原始姿势或稍后再试',
  'Throttling.RateQuota': '请求触发限流；请稍后再试',
  'Throttling.AllocationQuota': '平台姿势编辑服务繁忙，请稍后再试',
};

export function explainJobError(message: string, stage?: string): string {
  if (stage === 'rig' && /腾讯云错误 InvalidParameter/.test(message)) {
    const hint = '绑骨接口未接受输入模型；请核对角色姿态和 GLB 文件要求，具体原因可凭 RequestId 向腾讯云查询';
    const code = message.match(/腾讯云错误 InvalidParameter[A-Za-z0-9_.-]*/)?.[0];
    const requestId = message.match(/；RequestId=[A-Za-z0-9_.-]+/)?.[0] || '';
    return `${code}：${hint}${requestId}`;
  }
  const tencent = message.match(/腾讯云错误 ([A-Za-z0-9_.-]+)/);
  if (tencent) {
    const hint = tencentHints[tencent[1]] ||
      (tencent[1].startsWith('InvalidParameter') ? '请求参数不被接受；请核对当前步骤的输入与接口要求' : '服务暂不可用，请联系平台维护人员');
    const requestId = message.match(/；RequestId=[A-Za-z0-9_.-]+/)?.[0] || '';
    return `${tencent[0]}：${hint}${requestId}`;
  }
  const pose = message.match(/姿势 API 错误 ([A-Za-z0-9_.-]+)/);
  if (pose) {
    return `${pose[0]}：${poseHints[pose[1]] || '服务暂不可用，请联系平台维护人员'}`;
  }
  return message;
}
