/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";

export class ComponentScanWizard extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
        close: Function,
        validate: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        
        this.state = useState({
            selectedComponentId: null, // 用户选择的待登记组件ID
            selectedComponentName: '', // 用户选择的待登记组件名称
            selectedComponentCode: '', // 用户选择的待登记组件编码
            inputBarcode: '', // 输入框的值（支持手动输入）
            scannedBarcode: '',
            scannedProduct: null,
            scannedProductName: '',
            scannedProductCode: '',
            requiredComponents: [],
            verificationResult: 'pending',
            verificationMessage: '',
            isScanning: false,
        });

        onWillStart(async () => {
            // 先加载已选择的组件（从质检记录中）
            await this.loadSelectedComponent();
            // 如果质检记录中没有选择的组件，则从质检点配置中加载
            if (!this.state.selectedComponentId) {
                await this.loadConfiguredComponent();
            }
            // 如果既没有已选择的组件，也没有配置的组件，则从生产订单加载待登记组件列表
            if (!this.state.selectedComponentId) {
                await this.loadRequiredComponents();
            }
        });
    }

    async loadSelectedComponent() {
        // 从质检记录中加载已选择的组件
        const recordData = this.props.record.data;
        
        try {
            if (recordData.selected_component_id && recordData.selected_component_id[0]) {
                const product = await this.orm.read(
                    'product.product',
                    [recordData.selected_component_id[0]],
                    ['name', 'default_code']
                );
                
                if (product && product.length > 0) {
                    this.state.selectedComponentId = product[0].id;
                    this.state.selectedComponentName = product[0].name;
                    this.state.selectedComponentCode = product[0].default_code || '';
                }
            }
        } catch (error) {
            console.error('加载已选择的组件失败:', error);
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
            // 获取生产订单的待登记组件列表（只获取未完全消耗的组件）
            if (recordData.production_id && recordData.production_id[0]) {
                const production = await this.orm.read(
                    'mrp.production',
                    [recordData.production_id[0]],
                    ['name', 'move_raw_ids']
                );
                
                if (production && production.length > 0) {
                    const moveIds = production[0].move_raw_ids || [];
                    
                    if (moveIds.length > 0) {
                        // 读取移动记录，包括计划数量和已消耗数量
                        const moves = await this.orm.read(
                            'stock.move',
                            moveIds,
                            ['product_id', 'product_uom_qty', 'quantity', 'state']
                        );
                        
                        // 只获取待登记的组件（未完全消耗的组件）
                        // 条件：product_uom_qty > quantity（计划数量 > 已消耗数量）
                        const pendingMoves = moves.filter(move => {
                            const plannedQty = move.product_uom_qty || 0;
                            const consumedQty = move.quantity || 0;
                            // 只包含未完全消耗的组件，且状态为已分配或部分可用
                            return plannedQty > consumedQty && 
                                   move.state && 
                                   ['assigned', 'partially_available', 'done'].includes(move.state);
                        });
                        
                        const components = [];
                        for (const move of pendingMoves) {
                            if (move.product_id && move.product_id[0]) {
                                const product = await this.orm.read(
                                    'product.product',
                                    [move.product_id[0]],
                                    ['name', 'default_code']
                                );
                                
                                if (product && product.length > 0) {
                                    const plannedQty = move.product_uom_qty || 0;
                                    const consumedQty = move.quantity || 0;
                                    const remainingQty = plannedQty - consumedQty;
                                    
                                    components.push({
                                        id: product[0].id,
                                        name: product[0].name,
                                        code: product[0].default_code || '',
                                        quantity: remainingQty, // 显示剩余待登记数量
                                        plannedQty: plannedQty, // 计划数量
                                        consumedQty: consumedQty, // 已消耗数量
                                    });
                                }
                            }
                        }
                        
                        this.state.requiredComponents = components;
                    }
                }
            }
        } catch (error) {
            console.error('获取待登记组件列表失败:', error);
            this.notification.add('获取待登记组件列表失败', { type: 'danger' });
        }
    }
    
    async onSelectComponent(componentId) {
        // 用户选择待登记组件
        const component = this.state.requiredComponents.find(c => c.id === componentId);
        
        if (!component) {
            this.notification.add('选择的组件不存在', { type: 'danger' });
            return;
        }
        
        this.state.selectedComponentId = componentId;
        this.state.selectedComponentName = component.name;
        this.state.selectedComponentCode = component.code;
        
        // 保存到质检记录
        const recordData = this.props.record.data;
        try {
            await this.orm.write('quality.check', [recordData.id], {
                selected_component_id: componentId,
            });
            
            // 如果已经扫码过，重新验证
            if (this.state.scannedProduct) {
                await this.verifyComponent(this.state.scannedProduct);
            }
        } catch (error) {
            console.error('保存选择的组件失败:', error);
            this.notification.add('保存选择的组件失败', { type: 'danger' });
        }
    }

    onInputChange(ev) {
        // 监听输入变化，用于自动处理扫码（快速输入）
        // 手动输入时，用户应该使用 Enter 键或点击验证按钮
        const barcode = ev.target.value.trim();
        this.state.inputBarcode = barcode;
        
        if (!barcode) {
            return;
        }
        
        // 延迟处理，避免输入过程中的中间值触发
        clearTimeout(this._scanTimeout);
        this._scanTimeout = setTimeout(async () => {
            // 检查输入是否完整（扫码通常是快速输入，手动输入较慢）
            // 如果输入长度较长或包含空格，可能是手动输入，不自动处理
            if (barcode.length > 20 || barcode.includes(' ')) {
                // 可能是手动输入，不自动处理，等待用户按 Enter 或点击按钮
                return;
            }
            // 自动处理扫码（快速输入，通常是扫码）
            await this.processBarcode(barcode);
            this.state.inputBarcode = ''; // 清空输入框，准备下次扫码
        }, 500); // 500ms延迟，确保扫码完成（扫码通常很快）
    }
    
    async onManualVerify() {
        // 手动验证按钮点击事件
        const barcode = this.state.inputBarcode.trim();
        
        if (!barcode) {
            this.notification.add('请输入条码、产品编码或批次号', { type: 'warning' });
            return;
        }
        
        await this.processBarcode(barcode);
        this.state.inputBarcode = ''; // 清空输入框
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
            
            if (products && products.length > 0) {
                const product = products[0];
                this.state.scannedProduct = product.id;
                this.state.scannedProductName = product.name;
                this.state.scannedProductCode = product.default_code || product.barcode || '';
                
                // 验证组件是否匹配
                await this.verifyComponent(product.id);
            } else {
                // 尝试通过批次号查找
                console.log('[组件扫码确认] 未找到产品，尝试通过批次号查找:', barcode);
                const lots = await this.orm.searchRead(
                    'stock.lot',
                    [['name', '=', barcode]],
                    ['product_id'],
                    { limit: 1 }
                );
                
                console.log('[组件扫码确认] 批次号查询结果:', lots);
                
                if (lots && lots.length > 0 && lots[0].product_id) {
                    const productId = lots[0].product_id[0];
                    const product = await this.orm.read(
                        'product.product',
                        [productId],
                        ['name', 'default_code', 'barcode']
                    );
                    
                    if (product && product.length > 0) {
                        this.state.scannedProduct = product[0].id;
                        this.state.scannedProductName = product[0].name;
                        this.state.scannedProductCode = product[0].default_code || product[0].barcode || '';
                        
                        // 验证组件是否匹配
                        await this.verifyComponent(product[0].id);
                    } else {
                        // 批次号存在但产品不存在
                        console.log('[组件扫码确认] 批次号存在但产品不存在');
                        this.state.scannedProduct = null;
                        this.state.scannedProductName = '';
                        this.state.scannedProductCode = '';
                        this.state.verificationResult = 'mismatched';
                        this.state.verificationMessage = '批次号存在但对应的产品不存在';
                        this.notification.add('批次号存在但对应的产品不存在', { type: 'warning', sticky: true });
                    }
                } else {
                    // 未找到产品或批次号
                    console.log('[组件扫码确认] 未找到产品或批次号:', barcode);
                    this.state.scannedProduct = null;
                    this.state.scannedProductName = '';
                    this.state.scannedProductCode = '';
                    this.state.verificationResult = 'mismatched';
                    this.state.verificationMessage = `未找到匹配的产品或批次号: ${barcode}`;
                    this.notification.add(`未找到匹配的产品或批次号: ${barcode}`, { type: 'warning', sticky: true });
                }
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
        // 支持Enter键快速确认（pad设备扫码后通常会自动回车，或手动输入后按Enter）
        if (ev.key === 'Enter') {
            ev.preventDefault();
            // 取消自动处理的延迟
            if (this._scanTimeout) {
                clearTimeout(this._scanTimeout);
            }
            // 使用输入框的当前值
            const barcode = this.state.inputBarcode.trim();
            if (barcode) {
                await this.processBarcode(barcode);
                this.state.inputBarcode = ''; // 清空输入框
            }
        }
    }

    async verifyComponent(productId) {
        const recordData = this.props.record.data;
        
        // 检查是否已选择待登记组件
        if (!this.state.selectedComponentId) {
            this.notification.add('请先选择待登记的组件！', { type: 'warning' });
            return;
        }
        
        try {
            // 先保存扫码的组件到质检记录
            await this.orm.write('quality.check', [recordData.id], {
                scanned_component_id: productId,
                scanned_component_code: this.state.scannedProductCode,
            });
            
            // 调用后端验证方法
            const result = await this.orm.call(
                'quality.check',
                'verify_component',
                [recordData.id],
                {
                    scanned_component_id: productId,
                }
            );
            
            // 更新前端状态
            console.log('[组件扫码确认] 验证结果:', result);
            if (result && result.success === true) {
                this.state.verificationResult = 'matched';
                this.state.verificationMessage = result.message || '组件验证成功！';
                this.notification.add('组件验证成功', { type: 'success' });
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
        if (this.state.verificationResult === 'matched') {
            // 调用父类的 validate 方法继续流程
            if (this.props.validate) {
                return this.props.validate();
            }
        } else if (this.state.verificationResult === 'mismatched') {
            this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
        } else {
            this.notification.add('请先扫码确认组件', { type: 'warning' });
        }
    }
}

ComponentScanWizard.template = "mrp_component_scan_verify.ComponentScanWizard";

registry.category("components").add("ComponentScanWizard", ComponentScanWizard);

