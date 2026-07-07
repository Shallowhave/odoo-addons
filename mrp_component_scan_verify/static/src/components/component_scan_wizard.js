/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onMounted } from "@odoo/owl";

export class ComponentScanWizard extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
        close: Function,
        validate: Function,
        registerInstance: { type: Function, optional: true }, // 注册实例的回调函数
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        
        this.state = useState({
            selectedComponentId: null, // 用户选择的待登记组件ID
            selectedComponentName: '', // 用户选择的待登记组件名称
            selectedComponentCode: '', // 用户选择的待登记组件编码
            selectedMoveLineId: null, // 用户选择的待登记组件移动行ID
            selectedLotId: null, // 用户选择的待登记组件批次ID
            selectedLotName: '', // 用户选择的待登记组件批次名称
            selectedCandidateKey: null, // 用户选择的组件/批次候选项
            inputBarcode: '', // 输入框的值（支持手动输入）
            scannedBarcode: '',
            scannedProduct: null,
            scannedProductName: '',
            scannedProductCode: '',
            scannedLotId: null, // 扫码的批次号ID
            requiredComponents: [],
            verificationResult: 'pending',
            verificationMessage: '',
            isScanning: false,
            autoPassed: false, // 标记是否已自动通过
        });

        onWillStart(async () => {
            // 先加载已选择的组件（从质检记录中）
            await this.loadSelectedComponent();
            // 如果质检记录中没有选择的组件，则从质检点配置中加载
            if (!this.state.selectedComponentId) {
                await this.loadConfiguredComponent();
            }
            // 如果既没有已选择的组件，也没有配置的组件，则从生产订单加载待登记组件列表
            // 注意：即使已经选择了组件，也加载组件列表，以便用户可以切换组件（如果需要）
            // 但组件选择列表的显示由模板中的条件控制（只在没有选择组件时显示）
            await this.loadRequiredComponents();
        });
        
        // 注册组件实例到父组件
        // 使用 onMounted 确保组件完全初始化后再注册
        onMounted(() => {
            if (this.props.registerInstance) {
                console.log('[ComponentScanWizard] 注册实例到父组件');
                this.props.registerInstance(this);
            } else {
                console.warn('[ComponentScanWizard] registerInstance 回调不存在');
            }
        });
    }

    async loadSelectedComponent() {
        // 从质检记录中加载已选择的组件
        const recordData = this.props.record.data;
        
        try {
            // 如果 recordData 中没有 selected_component_id，尝试从后端读取完整的质检记录
            let selectedComponentId = null;
            if (recordData.selected_component_id && recordData.selected_component_id[0]) {
                selectedComponentId = recordData.selected_component_id[0];
            } else if (recordData.id) {
                // 从后端读取完整的质检记录，确保获取最新的 selected_component_id
                const check = await this.orm.read(
                    'quality.check',
                    [recordData.id],
                    ['selected_component_id']
                );
                if (check && check.length > 0 && check[0].selected_component_id && check[0].selected_component_id[0]) {
                    selectedComponentId = check[0].selected_component_id[0];
                }
            }
            
            if (selectedComponentId) {
                const product = await this.orm.read(
                    'product.product',
                    [selectedComponentId],
                    ['name', 'default_code']
                );
                
                if (product && product.length > 0) {
                    this.state.selectedComponentId = product[0].id;
                    this.state.selectedComponentName = product[0].name;
                    this.state.selectedComponentCode = product[0].default_code || '';
                    console.log('[组件扫码确认] 已加载选择的组件:', {
                        id: product[0].id,
                        name: product[0].name,
                        code: product[0].default_code
                    });
                }
            }
        } catch (error) {
            console.error('[组件扫码确认] 加载已选择的组件失败:', error);
        }
    }
    
    async loadConfiguredComponent() {
        // 从质检点配置中加载已配置的待登记组件
        const recordData = this.props.record.data;
        
        try {
            // 如果质检记录中已经有选择的组件，则不使用配置的组件
            if (this.state.selectedComponentId) {
                return;
            }
            
            // 如果 recordData.point_id 不存在，需要先读取完整的质检记录
            let pointId = null;
            if (recordData.point_id && recordData.point_id[0]) {
                pointId = recordData.point_id[0];
            } else if (recordData.id) {
                // 从质检记录中读取 point_id
                const check = await this.orm.read(
                    'quality.check',
                    [recordData.id],
                    ['point_id', 'component_id']
                );
                if (check && check.length > 0) {
                    if (check[0].point_id && check[0].point_id[0]) {
                        pointId = check[0].point_id[0];
                    }
                    // 如果质检记录中有 component_id，也使用它
                    if (check[0].component_id && check[0].component_id[0] && !recordData.component_id) {
                        recordData.component_id = check[0].component_id;
                    }
                }
            }
            
            // 读取质检点配置
            if (pointId) {
                // 方法1：直接使用 recordData.component_id（如果存在）
                if (recordData.component_id && recordData.component_id[0]) {
                    const productId = recordData.component_id[0];
                    const product = await this.orm.read(
                        'product.product',
                        [productId],
                        ['name', 'default_code']
                    );
                    
                    if (product && product.length > 0) {
                        // 自动选择配置的组件
                        this.state.selectedComponentId = product[0].id;
                        this.state.selectedComponentName = product[0].name;
                        this.state.selectedComponentCode = product[0].default_code || '';
                        
                        // 保存到质检记录
                        await this.orm.write('quality.check', [recordData.id], {
                            selected_component_id: product[0].id,
                        });
                    }
                } else {
                    // 方法2：如果 recordData.component_id 不存在，直接从质检点读取
                    const point = await this.orm.read(
                        'quality.point',
                        [pointId],
                        ['component_id']
                    );
                    
                    if (point && point.length > 0 && point[0].component_id) {
                        const productId = point[0].component_id[0];
                        const product = await this.orm.read(
                            'product.product',
                            [productId],
                            ['name', 'default_code']
                        );
                        
                        if (product && product.length > 0) {
                            // 自动选择配置的组件
                            this.state.selectedComponentId = product[0].id;
                            this.state.selectedComponentName = product[0].name;
                            this.state.selectedComponentCode = product[0].default_code || '';
                            
                            // 保存到质检记录
                            await this.orm.write('quality.check', [recordData.id], {
                                selected_component_id: product[0].id,
                            });
                        }
                    }
                }
            }
        } catch (error) {
            console.error('加载配置的组件失败:', error);
        }
    }
    
    async loadRequiredComponents() {
        const recordData = this.props.record.data;
        
        try {
            if (recordData.id) {
                this.state.requiredComponents = await this.orm.call(
                    'quality.check',
                    'get_component_scan_candidates',
                    [recordData.id],
                    {}
                );
            }
        } catch (error) {
            console.error('获取待登记组件列表失败:', error);
            this.notification.add('获取待登记组件列表失败', { type: 'danger' });
        }
    }
    
    async onSelectComponent(componentKey) {
        // 用户选择待登记组件/批次候选项
        const component = this.state.requiredComponents.find(c => c.key === componentKey);

        if (!component) {
            this.notification.add('选择的组件不存在', { type: 'danger' });
            return;
        }

        this.state.selectedComponentId = component.id;
        this.state.selectedComponentName = component.name;
        this.state.selectedComponentCode = component.code;
        this.state.selectedMoveLineId = component.move_line_id || null;
        this.state.selectedLotId = component.lot_id || null;
        this.state.selectedLotName = component.lot_name || '';
        this.state.selectedCandidateKey = component.key;

        // 保存到质检记录
        const recordData = this.props.record.data;
        try {
            await this.orm.write('quality.check', [recordData.id], {
                selected_component_id: component.id,
                selected_move_line_id: component.move_line_id || false,
                move_line_id: component.move_line_id || false,
                move_id: component.move_id || false,
                component_id: component.id,
            });

            // 如果已经扫码过，重新验证
            if (this.state.scannedProduct) {
                await this.verifyComponent(this.state.scannedProduct, this.state.scannedLotId || null);
            }
        } catch (error) {
            console.error('保存选择的组件失败:', error);
            this.notification.add('保存选择的组件失败', { type: 'danger' });
        }
    }

    onInputChange(ev) {
        // 更新输入框的值，确保扫码设备扫描的内容显示在输入框中
        // 用户需要按 Enter 键或点击验证按钮来触发验证
        const barcode = ev.target ? ev.target.value : ev;
        this.state.inputBarcode = barcode;
        
        // 清除之前的自动验证定时器（如果有）
        if (this._scanTimeout) {
            clearTimeout(this._scanTimeout);
            this._scanTimeout = null;
        }
        
        // 记录输入框的值变化（用于调试）
        if (barcode) {
            console.log('[组件扫码确认] 输入框值变化:', barcode);
        }
    }
    
    onKeyUp(ev) {
        // 监听 keyup 事件，确保扫码设备输入的内容被捕获
        // 有些扫码设备可能在 keyup 时才更新输入框的值
        if (ev.target && ev.target.value !== this.state.inputBarcode) {
            this.state.inputBarcode = ev.target.value;
            console.log('[组件扫码确认] keyup 事件，更新输入框值:', ev.target.value);
        }
    }
    
    onKeyPress(ev) {
        // 监听 keypress 事件，确保所有按键都被捕获
        // 扫码设备可能会快速输入多个字符
        if (ev.target && ev.target.value !== this.state.inputBarcode) {
            this.state.inputBarcode = ev.target.value;
        }
    }
    
    async onManualVerify() {
        // 手动验证按钮点击事件
        // 尝试从 DOM 元素获取最新的值（如果可能）
        let barcode = this.state.inputBarcode.trim();
        
        // 如果可能，尝试从 DOM 获取最新值
        try {
            const inputElement = document.querySelector('.o_component_scan_wizard input[type="text"]');
            if (inputElement && inputElement.value) {
                const domValue = inputElement.value.trim();
                if (domValue && domValue !== barcode) {
                    barcode = domValue;
                    this.state.inputBarcode = domValue;
                    console.log('[组件扫码确认] 从 DOM 获取输入框值:', domValue);
                }
            }
        } catch (e) {
            console.warn('[组件扫码确认] 无法从 DOM 获取输入框值:', e);
        }
        
        if (!barcode) {
            this.notification.add('请输入条码、产品编码或批次号', { type: 'warning' });
            return;
        }
        
        // 确保条码显示在输入框中（扫码设备扫描的内容应该已经显示）
        // 然后触发验证
        await this.processBarcode(barcode);
        // 验证完成后才清空输入框（成功或失败都清空，准备下次扫码）
        this.state.inputBarcode = '';
    }
    
    async processBarcode(barcode) {
        if (!barcode) {
            return;
        }
        
        this.state.isScanning = true;
        this.state.scannedBarcode = barcode;
        this.state.verificationResult = 'pending'; // 重置验证结果状态
        this.state.verificationMessage = ''; // 清空验证消息
        
        try {
            console.log('[组件扫码确认] 开始处理条码:', barcode);
            // 通过条码查找产品
            const products = await this.orm.searchRead(
                'product.product',
                [
                    '|',
                    ['barcode', '=', barcode],
                    ['default_code', '=', barcode]
                ],
                ['name', 'default_code', 'barcode'],
                { limit: 1 }
            );
            
            console.log('[组件扫码确认] 产品查询结果:', products);
            
            // **关键修复**：先尝试通过批次号查找（因为批次号更精确）
            const lots = await this.orm.searchRead(
                'stock.lot',
                [['name', '=', barcode]],
                ['product_id', 'id'],
                { limit: 1 }
            );
            
            console.log('[组件扫码确认] 批次号查询结果:', lots);
            
            if (lots && lots.length > 0 && lots[0].product_id) {
                // 通过批次号找到了产品
                const productId = lots[0].product_id[0];
                const lotId = lots[0].id;
                const product = await this.orm.read(
                    'product.product',
                    [productId],
                    ['name', 'default_code', 'barcode']
                );
                
                if (product && product.length > 0) {
                    this.state.scannedProduct = product[0].id;
                    this.state.scannedProductName = product[0].name;
                    this.state.scannedProductCode = product[0].default_code || product[0].barcode || '';
                    this.state.scannedLotId = lotId; // 保存批次号ID
                    
                    // 验证组件是否匹配（传递批次号ID）
                    await this.verifyComponent(product[0].id, lotId);
                } else {
                    // 批次号存在但产品不存在
                    console.log('[组件扫码确认] 批次号存在但产品不存在');
                    this.state.scannedProduct = null;
                    this.state.scannedProductName = '';
                    this.state.scannedProductCode = '';
                    this.state.scannedLotId = null;
                    this.state.verificationResult = 'mismatched';
                    this.state.verificationMessage = '批次号存在但对应的产品不存在';
                    this.notification.add('批次号存在但对应的产品不存在', { type: 'warning', sticky: true });
                }
            } else if (products && products.length > 0) {
                // 通过产品条码/编码找到了产品（没有批次号）
                const product = products[0];
                this.state.scannedProduct = product.id;
                this.state.scannedProductName = product.name;
                this.state.scannedProductCode = product.default_code || product.barcode || '';
                this.state.scannedLotId = null; // 没有批次号
                
                // 验证组件是否匹配（没有批次号）
                await this.verifyComponent(product.id, null);
            } else {
                // 未找到产品或批次号
                console.log('[组件扫码确认] 未找到产品或批次号:', barcode);
                this.state.scannedProduct = null;
                this.state.scannedProductName = '';
                this.state.scannedProductCode = '';
                this.state.scannedLotId = null;
                this.state.verificationResult = 'mismatched';
                this.state.verificationMessage = `未找到匹配的产品或批次号: ${barcode}`;
                this.notification.add(`未找到匹配的产品或批次号: ${barcode}`, { type: 'warning', sticky: true });
            }
        } catch (error) {
            console.error('扫码验证失败:', error);
            this.notification.add('扫码验证失败: ' + String(error), { type: 'danger', sticky: true });
            this.state.verificationResult = 'mismatched';
            this.state.verificationMessage = '验证失败: ' + String(error);
        } finally {
            this.state.isScanning = false;
        }
    }
    
    async onKeyDown(ev) {
        // 支持Enter键触发验证（pad设备扫码后通常会自动回车，或手动输入后按Enter）
        if (ev.key === 'Enter') {
            ev.preventDefault();
            
            // 清除自动验证定时器（如果有）
            if (this._scanTimeout) {
                clearTimeout(this._scanTimeout);
                this._scanTimeout = null;
            }
            
            // 确保从输入框获取最新的值（扫码设备可能在 Enter 之前才完成输入）
            const inputValue = ev.target ? ev.target.value : this.state.inputBarcode;
            const barcode = inputValue.trim();
            
            // 更新状态，确保值同步
            if (inputValue !== this.state.inputBarcode) {
                this.state.inputBarcode = inputValue;
                console.log('[组件扫码确认] Enter 键，同步输入框值:', inputValue);
            }
            
            if (barcode) {
                // 先显示在输入框中（确保扫码设备扫描的内容可见）
                // 然后触发验证
                await this.processBarcode(barcode);
                // 验证完成后才清空输入框（成功或失败都清空，准备下次扫码）
                this.state.inputBarcode = '';
            } else {
                this.notification.add('请输入条码、产品编码或批次号', { type: 'warning' });
            }
        }
    }

    async verifyComponent(productId, lotId = null) {
        const recordData = this.props.record.data;
        
        // 检查是否已选择待登记组件
        if (!this.state.selectedComponentId) {
            this.notification.add('请先选择待登记的组件！', { type: 'warning' });
            return;
        }
        
        try {
            // 先保存扫码的组件和批次号到质检记录
            const writeData = {
                scanned_component_id: productId,
                scanned_component_code: this.state.scannedProductCode,
                scanned_lot_id: lotId || false,
            };
            await this.orm.write('quality.check', [recordData.id], writeData);
            
            // 调用后端验证方法（传递批次号ID）
            const result = await this.orm.call(
                'quality.check',
                'verify_component',
                [recordData.id],
                {
                    scanned_component_id: productId,
                    scanned_lot_id: lotId,
                }
            );
            
            // 更新前端状态
            console.log('[组件扫码确认] 验证结果:', result);
            if (result && result.success === true) {
                this.state.verificationResult = 'matched';
                this.state.verificationMessage = result.message || '组件验证成功！';
                this.notification.add('组件验证成功，请点击验证按钮完成质检', { type: 'success' });
                
                // 如果自动通过，标记状态，但不自动调用 validate（让用户手动点击验证按钮）
                if (result.auto_passed === true) {
                    console.log('[组件扫码确认] 自动通过质检，等待用户点击验证按钮');
                    this.state.autoPassed = true; // 标记已自动通过
                }
            } else {
                // 验证失败或不匹配
                this.state.verificationResult = 'mismatched';
                this.state.verificationMessage = result && result.message ? result.message : '组件验证失败！组件不匹配。';
                console.log('[组件扫码确认] 验证失败:', this.state.verificationMessage);
                this.notification.add(this.state.verificationMessage, { type: 'danger', sticky: true });
            }
            
            // 重新读取质检记录以获取最新的验证结果
            const check = await this.orm.read(
                'quality.check',
                [recordData.id],
                ['component_verification_result', 'component_verification_message']
            );
            
            if (check && check.length > 0) {
                this.state.verificationResult = check[0].component_verification_result || 'pending';
                this.state.verificationMessage = check[0].component_verification_message || '';
            }
        } catch (error) {
            console.error('验证组件失败:', error);
            this.notification.add('验证组件失败: ' + String(error), { type: 'danger', sticky: true });
            this.state.verificationResult = 'mismatched';
            this.state.verificationMessage = '验证失败: ' + String(error);
        }
    }

    async onValidate() {
        console.log('[组件扫码确认] onValidate 被调用');
        console.log('[组件扫码确认] 当前状态:', {
            autoPassed: this.state.autoPassed,
            verificationResult: this.state.verificationResult,
            inputBarcode: this.state.inputBarcode,
            selectedComponentId: this.state.selectedComponentId,
            scannedProduct: this.state.scannedProduct,
        });
        
        // **关键修复**：检查是否已选择待登记组件
        if (!this.state.selectedComponentId) {
            console.log('[组件扫码确认] 未选择待登记组件，返回 false');
            this.notification.add('请先选择待登记的组件！', { type: 'warning' });
            return false;
        }
        
        // 如果已自动通过，还需要再次验证确保数据一致性
        if (this.state.autoPassed) {
            // 重新从后端读取验证结果，确保数据一致性
            const recordData = this.props.record.data;
            try {
                const check = await this.orm.read(
                    'quality.check',
                    [recordData.id],
                    ['component_verification_result', 'scanned_component_id', 'selected_component_id']
                );
                
                if (check && check.length > 0) {
                    const verificationResult = check[0].component_verification_result || 'pending';
                    const scannedComponentId = check[0].scanned_component_id && check[0].scanned_component_id[0];
                    const selectedComponentId = check[0].selected_component_id && check[0].selected_component_id[0];
                    
                    // 如果验证结果不是 matched，或者扫码的组件与选择的组件不匹配，阻止通过
                    if (verificationResult !== 'matched') {
                        console.log('[组件扫码确认] 后端验证结果不是 matched，返回 false');
                        this.notification.add('组件验证失败，无法通过验证', { type: 'danger' });
                        return false;
                    }
                    
                    // 双重验证：确保扫码的组件ID与选择的组件ID匹配
                    if (scannedComponentId && selectedComponentId && scannedComponentId !== selectedComponentId) {
                        console.log('[组件扫码确认] 扫码的组件与选择的组件不匹配，返回 false');
                        this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
                        return false;
                    }
                }
            } catch (error) {
                console.error('[组件扫码确认] 读取验证结果失败:', error);
                // 如果读取失败，为了安全起见，阻止通过
                this.notification.add('无法验证组件，请重新扫码', { type: 'warning' });
                return false;
            }
            
            console.log('[组件扫码确认] 已自动通过且验证通过，返回 true');
            return true;
        }
        
        // 如果还没有验证过，尝试从输入框获取条码并处理
        if (this.state.verificationResult !== 'matched') {
            // 先尝试从 DOM 获取最新的输入框值（多种选择器）
            let barcode = this.state.inputBarcode ? this.state.inputBarcode.trim() : '';
            
            // 尝试多种方式获取输入框的值
            try {
                // 方法1: 通过类名查找
                let inputElement = document.querySelector('.o_component_scan_wizard input[type="text"]');
                // 方法2: 如果方法1没找到，尝试查找所有输入框
                if (!inputElement) {
                    inputElement = document.querySelector('.workorder_component_scan input[type="text"]');
                }
                // 方法3: 如果还是没找到，尝试查找所有文本输入框
                if (!inputElement) {
                    const allInputs = document.querySelectorAll('input[type="text"]');
                    // 查找包含条码值的输入框
                    for (const input of allInputs) {
                        if (input.value && input.value.length > 5) {
                            inputElement = input;
                            break;
                        }
                    }
                }
                
                if (inputElement && inputElement.value) {
                    const domValue = inputElement.value.trim();
                    if (domValue) {
                        barcode = domValue;
                        this.state.inputBarcode = domValue;
                        console.log('[组件扫码确认] 从 DOM 获取输入框值:', domValue);
                    }
                } else {
                    console.log('[组件扫码确认] 未找到输入框或输入框为空');
                }
            } catch (e) {
                console.warn('[组件扫码确认] 无法从 DOM 获取输入框值:', e);
            }
            
            console.log('[组件扫码确认] 最终获取的条码:', barcode);
            
            // 如果有条码，先处理条码
            if (barcode) {
                console.log('[组件扫码确认] 点击验证按钮，先处理条码:', barcode);
                await this.processBarcode(barcode);
                // 清空输入框
                this.state.inputBarcode = '';
                // 处理完条码后，检查验证结果
                if (this.state.verificationResult === 'matched') {
                    console.log('[组件扫码确认] 验证成功，返回 true');
                    return true; // 返回 true，让父组件继续调用 super.validate()
                } else {
                    // 验证失败，阻止继续
                    console.log('[组件扫码确认] 验证失败，返回 false');
                    return false;
                }
            } else {
                // 没有条码，提示用户
                console.log('[组件扫码确认] 没有条码，提示用户');
                this.notification.add('请先扫码确认组件', { type: 'warning' });
                return false;
            }
        }
        
        // **关键修复**：如果验证成功，还需要再次验证确保数据一致性
        if (this.state.verificationResult === 'matched') {
            // 重新从后端读取验证结果，确保数据一致性
            const recordData = this.props.record.data;
            try {
                const check = await this.orm.read(
                    'quality.check',
                    [recordData.id],
                    ['component_verification_result', 'scanned_component_id', 'selected_component_id']
                );
                
                if (check && check.length > 0) {
                    const verificationResult = check[0].component_verification_result || 'pending';
                    const scannedComponentId = check[0].scanned_component_id && check[0].scanned_component_id[0];
                    const selectedComponentId = check[0].selected_component_id && check[0].selected_component_id[0];
                    
                    // 如果验证结果不是 matched，阻止通过
                    if (verificationResult !== 'matched') {
                        console.log('[组件扫码确认] 后端验证结果不是 matched，返回 false');
                        this.notification.add('组件验证失败，无法通过验证', { type: 'danger' });
                        return false;
                    }
                    
                    // 双重验证：确保扫码的组件ID与选择的组件ID匹配
                    if (scannedComponentId && selectedComponentId && scannedComponentId !== selectedComponentId) {
                        console.log('[组件扫码确认] 扫码的组件与选择的组件不匹配，返回 false');
                        this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
                        return false;
                    }
                    
                    // 如果前端状态中的扫码产品与选择的组件不匹配，也阻止通过
                    if (this.state.scannedProduct && this.state.selectedComponentId && 
                        this.state.scannedProduct !== this.state.selectedComponentId) {
                        console.log('[组件扫码确认] 前端状态中组件不匹配，返回 false');
                        this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
                        return false;
                    }
                }
            } catch (error) {
                console.error('[组件扫码确认] 读取验证结果失败:', error);
                // 如果读取失败，为了安全起见，阻止通过
                this.notification.add('无法验证组件，请重新扫码', { type: 'warning' });
                return false;
            }
            
            console.log('[组件扫码确认] 验证已成功，返回 true');
            return true;
        } else if (this.state.verificationResult === 'mismatched') {
            console.log('[组件扫码确认] 验证失败，返回 false');
            this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
            return false; // 阻止继续
        } else {
            console.log('[组件扫码确认] 未验证，返回 false');
            this.notification.add('请先扫码确认组件', { type: 'warning' });
            return false; // 阻止继续
        }
    }
}

ComponentScanWizard.template = "mrp_component_scan_verify.ComponentScanWizard";

registry.category("components").add("ComponentScanWizard", ComponentScanWizard);

